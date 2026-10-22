use axum::{
    Json,
    body::to_bytes,
    extract::{Request, State},
    http::{HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use hmac::{Hmac, Mac};
use minijinja::{Environment, Value as TemplateValue, value::Kwargs};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::{Column, MySqlPool, Row, TypeInfo, mysql::MySqlRow};
use std::{collections::HashMap, path::PathBuf, sync::Arc};
#[path = "site_account.rs"]
mod account;
#[path = "site_admin.rs"]
mod admin;
#[path = "site_auth.rs"]
mod auth;
#[path = "site_lumi.rs"]
mod lumi;
#[path = "site_misc.rs"]
mod misc;
#[path = "site_personal.rs"]
mod personal;
#[path = "site_public.rs"]
mod public;
#[path = "site_social.rs"]
mod social;
#[path = "site_tasks.rs"]
pub mod tasks;
#[path = "site_uploads.rs"]
mod uploads;
#[path = "site_upstream.rs"]
mod upstream;

pub fn env(key: &str, fallback: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| fallback.into())
}
pub struct Site {
    pub bots: std::sync::OnceLock<Arc<crate::bots::Bots>>,
    pub db: MySqlPool,
    pub templates: Environment<'static>,
    pub members: Value,
    pub videos: arc_swap::ArcSwap<Vec<Value>>,
    pub secret: Vec<u8>,
    pub root: PathBuf,
    pub client: reqwest::Client,
    pub cache: std::sync::Mutex<HashMap<String, (std::time::Instant, Value, usize)>>,
    pub controls: std::sync::RwLock<Controls>,
    pub live: std::sync::RwLock<Value>,
    pub geoip: Option<maxminddb::Reader<Vec<u8>>>,
    pub analytics: tokio::sync::mpsc::Sender<(String, String)>,
    pub analytics_receiver: std::sync::Mutex<Option<tokio::sync::mpsc::Receiver<(String, String)>>>,
}
#[derive(Default, Clone)]
pub struct Controls {
    pub settings: HashMap<String, String>,
    pub banned: std::collections::HashSet<String>,
    pub blocked: Vec<String>,
}
impl Site {
    pub async fn auto_ban(&self, ip: std::net::IpAddr, reason: &str) -> Result<bool, sqlx::Error> {
        if ip.is_loopback() || ip.is_unspecified() || ip.is_multicast() {
            return Ok(false);
        }
        let ip = ip.to_string();
        if self.controls.read().unwrap().banned.contains(&ip) {
            return Ok(true);
        }
        self.execute(
            "INSERT IGNORE INTO banned_ips (ip,reason) VALUES (?,?)",
            &[json!(ip), json!(reason)],
        )
        .await?;
        self.controls.write().unwrap().banned.insert(ip);
        Ok(true)
    }
    pub fn new(root: PathBuf, db: MySqlPool) -> Result<Self, Box<dyn std::error::Error>> {
        let members: Value = serde_json::from_str(include_str!("../resources/members.json"))?;
        let mut templates = Environment::new();
        templates.set_loader(minijinja::path_loader(root.join("templates")));
        templates.set_unknown_method_callback(minijinja_contrib::pycompat::unknown_method_callback);
        for (key, value) in members.as_object().unwrap() {
            templates.add_global(key.clone(), TemplateValue::from_serialize(value));
        }
        let host = env("SITE_HOST", "pastellive.co.kr");
        templates.add_function(
            "url_for",
            move |endpoint: String, kwargs: Kwargs| -> Result<TemplateValue, minijinja::Error> {
                let filename: String = kwargs.get("filename")?;
                let external: bool = kwargs.get("_external").unwrap_or(false);
                if endpoint != "static" {
                    return Ok(TemplateValue::from_safe_string(String::new()));
                }
                Ok(TemplateValue::from_safe_string(format!(
                    "{}/static/{filename}",
                    if external {
                        format!("https://{host}")
                    } else {
                        String::new()
                    }
                )))
            },
        );
        let assets = root.join("static");
        let hashes = std::sync::Mutex::new(HashMap::<String, String>::new());
        templates.add_function("versioned_static", move |name: String| {
            let mut hashes = hashes.lock().unwrap();
            if let Some(hash) = hashes.get(&name) {
                return TemplateValue::from_safe_string(format!("/static/{name}?v={hash}"));
            }
            let hash = std::fs::read(assets.join(&name))
                .map(|bytes| format!("{:x}", Sha256::digest(bytes)))
                .unwrap_or_default();
            let hash = hash[..hash.len().min(10)].to_owned();
            hashes.insert(name.clone(), hash.clone());
            TemplateValue::from_safe_string(format!("/static/{name}?v={hash}"))
        });
        let videos = serde_json::from_slice::<Vec<Value>>(&std::fs::read(
            root.join("data/video_pool_cache.json"),
        )?)?;
        let secret = env("SECRET_KEY", "");
        let secret = if secret.is_empty() {
            std::fs::read_to_string(root.join(".secret_key"))?
                .trim()
                .as_bytes()
                .to_vec()
        } else {
            secret.into_bytes()
        };
        let client = reqwest::Client::builder()
            .no_proxy()
            .connect_timeout(std::time::Duration::from_secs(3))
            .timeout(std::time::Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::none())
            .user_agent("Mozilla/5.0 Luminous/1.0")
            .build()?;
        let geoip =
            maxminddb::Reader::open_readfile(root.join("data/geoip/dbip-country-lite.mmdb")).ok();
        let (analytics, receiver) = tokio::sync::mpsc::channel(256);
        Ok(Self {
            bots: std::sync::OnceLock::new(),
            db,
            templates,
            members,
            videos: arc_swap::ArcSwap::from_pointee(videos),
            secret,
            root,
            client,
            geoip,
            analytics,
            analytics_receiver: std::sync::Mutex::new(Some(receiver)),
            cache: std::sync::Mutex::new(HashMap::new()),
            controls: std::sync::RwLock::new(Controls::default()),
            live: std::sync::RwLock::new(json!({"status":{},"details":{}})),
        })
    }
    pub async fn query(&self, sql: &str, args: &[Value]) -> Result<Vec<Value>, sqlx::Error> {
        let mut q = sqlx::query(sql);
        for arg in args {
            q = match arg {
                Value::Number(n) => q.bind(n.as_i64().unwrap_or(0)),
                Value::Bool(b) => q.bind(*b),
                Value::Null => q.bind(Option::<String>::None),
                _ => q.bind(arg.as_str().unwrap_or("").to_string()),
            };
        }
        Ok(q.fetch_all(&self.db).await?.iter().map(row_json).collect())
    }
    pub fn country(&self, ip: &str) -> String {
        ip.parse()
            .ok()
            .and_then(|ip| {
                self.geoip
                    .as_ref()?
                    .lookup(ip)
                    .ok()?
                    .decode::<maxminddb::geoip2::Country>()
                    .ok()
                    .flatten()?
                    .country
                    .iso_code
                    .map(str::to_owned)
            })
            .unwrap_or_default()
    }
    pub fn protected_bot(&self, ip: &str, reason: &str) -> bool {
        reason.starts_with("자동 차단: 알려진 봇")
            || ip
                .parse()
                .ok()
                .is_some_and(|ip| self.bots.get().is_some_and(|b| b.matches(ip)))
    }
    pub async fn refresh_controls(&self) -> Result<(), sqlx::Error> {
        let settings = self
            .query("SELECT setting_key,setting_value FROM site_settings", &[])
            .await?
            .iter()
            .map(|v| {
                (
                    v["setting_key"].as_str().unwrap_or("").to_string(),
                    v["setting_value"].as_str().unwrap_or("").to_string(),
                )
            })
            .collect();
        let banned = self
            .query("SELECT ip FROM banned_ips", &[])
            .await?
            .iter()
            .filter_map(|v| v["ip"].as_str().map(str::to_string))
            .collect();
        let blocked = self
            .query("SELECT path FROM blocked_paths", &[])
            .await?
            .iter()
            .filter_map(|v| v["path"].as_str().map(str::to_string))
            .collect();
        *self.controls.write().unwrap() = Controls {
            settings,
            banned,
            blocked,
        };
        Ok(())
    }
    pub async fn execute(&self, sql: &str, args: &[Value]) -> Result<u64, sqlx::Error> {
        let mut q = sqlx::query(sql);
        for arg in args {
            q = match arg {
                Value::Number(n) => q.bind(n.as_i64().unwrap_or(0)),
                Value::Bool(b) => q.bind(*b),
                Value::Null => q.bind(Option::<String>::None),
                _ => q.bind(arg.as_str().unwrap_or("").to_string()),
            };
        }
        Ok(q.execute(&self.db).await?.last_insert_id())
    }
    pub fn render(&self, name: &str, context: Value) -> Response {
        match self
            .templates
            .get_template(name)
            .and_then(|t| t.render(context))
        {
            Ok(html) => Html(html).into_response(),
            Err(err) => {
                eprintln!("template {name}: {err}");
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "화면을 불러오지 못했습니다.",
                )
                    .into_response()
            }
        }
    }
    pub fn session(&self, headers: &HeaderMap) -> Value {
        let cookie = headers
            .get("cookie")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        for cookie in cookie.split(';') {
            let Some(value) = cookie.trim().strip_prefix("pl_session=") else {
                continue;
            };
            let Some((payload, sig)) = value.split_once('.') else {
                continue;
            };
            let Ok(sig) = URL_SAFE_NO_PAD.decode(sig) else {
                continue;
            };
            let mut mac = Hmac::<Sha256>::new_from_slice(&self.secret).unwrap();
            mac.update(payload.as_bytes());
            if mac.verify_slice(&sig).is_err() {
                continue;
            }
            if let Ok(data) = URL_SAFE_NO_PAD.decode(payload).and_then(|v| Ok(v)) {
                if let Ok(session) = serde_json::from_slice(&data) {
                    return session;
                }
            }
        }
        json!({})
    }
    pub fn cookie(&self, session: &Value) -> String {
        let payload = URL_SAFE_NO_PAD.encode(serde_json::to_vec(session).unwrap());
        let mut mac = Hmac::<Sha256>::new_from_slice(&self.secret).unwrap();
        mac.update(payload.as_bytes());
        let sig = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
        let domain = env("COOKIE_DOMAIN", ".pastellive.co.kr");
        format!(
            "pl_session={payload}.{sig}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000{}{}",
            if domain.is_empty() {
                String::new()
            } else {
                format!("; Domain={domain}")
            },
            if env("COOKIE_SECURE", "true") == "true" {
                "; Secure"
            } else {
                ""
            }
        )
    }
    pub async fn fetch_json(
        &self,
        url: &str,
        params: &[(&str, String)],
        ttl: u64,
    ) -> Option<Value> {
        use futures_util::StreamExt;
        let key = format!("{url}|{params:?}");
        if let Some((at, value, _)) = self.cache.lock().unwrap().get(&key).cloned() {
            if at.elapsed().as_secs() < ttl {
                return Some(value);
            }
        }
        let _permit = upstream::UPSTREAM.try_acquire().ok()?;
        let response = self
            .client
            .get(url)
            .query(params)
            .timeout(std::time::Duration::from_secs(8))
            .send()
            .await
            .ok()?;
        if !response.status().is_success() {
            return None;
        }
        let mut bytes = Vec::new();
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.ok()?;
            if bytes.len() + chunk.len() > 2 * 1024 * 1024 {
                return None;
            }
            bytes.extend_from_slice(&chunk);
        }
        let value: Value = serde_json::from_slice(&bytes).ok()?;
        let mut cache = self.cache.lock().unwrap();
        if cache.len() >= 256
            || cache.values().map(|(_, _, n)| n).sum::<usize>() + bytes.len() > 16 * 1024 * 1024
        {
            cache.retain(|_, (at, _, _)| at.elapsed().as_secs() < 30);
            if cache.len() >= 256
                || cache.values().map(|(_, _, n)| n).sum::<usize>() + bytes.len() > 16 * 1024 * 1024
            {
                cache.clear();
            }
        }
        cache.insert(key, (std::time::Instant::now(), value.clone(), bytes.len()));
        Some(value)
    }
}
pub fn row_json(row: &MySqlRow) -> Value {
    let mut value = serde_json::Map::new();
    for (i, c) in row.columns().iter().enumerate() {
        let typ = c.type_info().name();
        let val = match typ {
            "BOOLEAN" | "TINYINT" | "SMALLINT" | "MEDIUMINT" | "INT" | "BIGINT" => row
                .try_get::<i64, _>(i)
                .map(Value::from)
                .unwrap_or(Value::Null),
            "BIGINT UNSIGNED" | "INT UNSIGNED" => row
                .try_get::<u64, _>(i)
                .map(Value::from)
                .unwrap_or(Value::Null),
            "FLOAT" | "DOUBLE" => row
                .try_get::<f64, _>(i)
                .map(Value::from)
                .unwrap_or(Value::Null),
            "DECIMAL" => row
                .try_get::<bigdecimal::BigDecimal, _>(i)
                .ok()
                .and_then(|d| serde_json::from_str(&d.to_string()).ok())
                .unwrap_or(Value::Null),
            "DATETIME" | "TIMESTAMP" => row
                .try_get::<chrono::NaiveDateTime, _>(i)
                .map(|d| json!(d.format("%Y-%m-%d %H:%M:%S").to_string()))
                .unwrap_or(Value::Null),
            "DATE" => row
                .try_get::<chrono::NaiveDate, _>(i)
                .map(|d| json!(d.to_string()))
                .unwrap_or(Value::Null),
            _ => row
                .try_get::<String, _>(i)
                .map(Value::from)
                .unwrap_or(Value::Null),
        };
        value.insert(c.name().into(), val);
    }
    Value::Object(value)
}
pub fn output(value: Value) -> Response {
    Json(value).into_response()
}
pub fn error(status: StatusCode, message: &str) -> Response {
    (
        status,
        Json(json!({"success":false,"message":message,"error":message})),
    )
        .into_response()
}
pub fn query_params(uri: &axum::http::Uri) -> HashMap<String, String> {
    uri.query()
        .unwrap_or("")
        .split('&')
        .filter_map(|p| p.split_once('='))
        .map(|(k, v)| {
            (
                k.to_string(),
                percent_encoding::percent_decode_str(&v.replace('+', " "))
                    .decode_utf8_lossy()
                    .into_owned(),
            )
        })
        .collect()
}
pub async fn dispatch(State(site): State<Arc<Site>>, request: Request) -> Response {
    let videos = site.videos.load_full();
    let path = request.uri().path().to_string();
    let method = if request.method() == axum::http::Method::HEAD {
        axum::http::Method::GET
    } else {
        request.method().clone()
    };
    let params = query_params(request.uri());
    if method == axum::http::Method::GET && !path.starts_with("/api/") {
        let ip = request
            .headers()
            .get("cf-connecting-ip")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("127.0.0.1");
        if ip.parse::<std::net::IpAddr>().is_ok() {
            let _ = site
                .analytics
                .try_send((path.chars().take(255).collect(), ip.into()));
        }
    }
    let mut session = site.session(request.headers());
    if let Some(id) = session["user_id"].as_i64() {
        match site
            .query(
                "SELECT suspended,session_epoch FROM users WHERE id=?",
                &[json!(id)],
            )
            .await
        {
            Ok(rows)
                if rows.first().is_some_and(|u| {
                    u["suspended"] == 0
                        && u["session_epoch"].as_i64().unwrap_or(0)
                            == session["sv"].as_i64().unwrap_or(0)
                }) => {}
            Ok(_) => session = json!({}),
            Err(_) => {
                return error(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "회원 상태를 확인하지 못했습니다.",
                );
            }
        }
    }
    let user_id = session["user_id"].as_i64().unwrap_or(0);
    let current_user = if user_id != 0 {
        site.query(
            "SELECT id,nickname,email,staff_role FROM users WHERE id=?",
            &[json!(user_id)],
        )
        .await
        .ok()
        .and_then(|rows| rows.into_iter().next())
        .unwrap_or(json!({}))
    } else {
        json!({})
    };
    let is_admin = auth::admin(&current_user);
    if !is_admin
        && current_user["staff_role"] != "개발자"
        && path != "/api/site/notice"
        && ![
            "/api/login",
            "/api/me",
            "/logout",
            "/login/discord",
            "/login/discord/callback",
        ]
        .contains(&path.as_str())
        && !path.starts_with("/static/")
    {
        let controls = site.controls.read().unwrap();
        if controls
            .settings
            .get("maintenance_enabled")
            .is_some_and(|v| v == "1" || v == "true")
        {
            return error(
                StatusCode::SERVICE_UNAVAILABLE,
                "사이트 점검 중입니다. 잠시 후 다시 방문해주세요.",
            );
        }
    }
    let host = request
        .headers()
        .get("host")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .split(':')
        .next()
        .unwrap_or("");
    if host == env("ADMIN_HOSTNAME", "admin.pastellive.co.kr") {
        if !is_admin {
            return axum::response::Redirect::to(&format!(
                "https://{}/?admin_denied=1",
                env("SITE_HOST", "pastellive.co.kr")
            ))
            .into_response();
        }
        if path == "/" {
            return axum::response::Redirect::to("/admin/stats").into_response();
        }
    }
    if path == "/admin/stats" {
        return if is_admin {
            site.render("admin_stats.html", json!({}))
        } else {
            axum::response::Redirect::to("/").into_response()
        };
    }
    let mobile = request
        .headers()
        .get("user-agent")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_lowercase()
        .contains("mobile");
    if method == axum::http::Method::GET {
        if path == "/profile" {
            if user_id == 0 {
                return axum::response::Redirect::to("/").into_response();
            }
            return match site.query("SELECT id,nickname,picture,email,login_id,discord_id,discord_username,total_attendance,consecutive_attendance,last_attendance_date,channel_id,channel_description,channel_link,channel_country,staff_role,created_at FROM users WHERE id=?",&[json!(user_id)]).await {
                Ok(users) if !users.is_empty()=>site.render("profile.html",json!({"user":users[0],"attendance_infinite":users[0]["staff_role"]=="개발자"})),
                _=>error(StatusCode::SERVICE_UNAVAILABLE,"회원 정보를 불러오지 못했습니다."),
            };
        }
        if let Some(id) = path.strip_prefix("/channel/") {
            if site
                .query("SELECT id FROM users WHERE channel_id=?", &[json!(id)])
                .await
                .unwrap_or_default()
                .is_empty()
            {
                return error(StatusCode::NOT_FOUND, "존재하지 않는 채널입니다.");
            }
            return site.render(
                "channel.html",
                json!({"channel_id":id,"request":{"path":path}}),
            );
        }
        if matches!(
            path.as_str(),
            "/" | "/gallery" | "/playlists" | "/bookmarks"
        ) || path.ends_with("/community")
        {
            let long: Vec<_> = videos
                .iter()
                .filter(|v| v["is_short"] != true)
                .take(24)
                .collect();
            let shorts: Vec<_> = videos
                .iter()
                .filter(|v| v["is_short"] == true)
                .take(15)
                .collect();
            let today = (chrono::Utc::now() + chrono::Duration::hours(9))
                .date_naive()
                .to_string();
            let mut schedules=site.query("SELECT member_name,title,TIME_FORMAT(event_time,'%H:%i') AS event_time,is_day_off FROM member_schedules WHERE event_date=? ORDER BY event_time,id",&[json!(today)]).await.unwrap_or_default();
            for s in &mut schedules {
                s["is_day_off"] = json!(s["is_day_off"] == 1);
                s["is_live"] = json!(
                    s["is_day_off"] != true
                        && site.live.read().unwrap()["status"]
                            [s["member_name"].as_str().unwrap_or("")]
                            == true
                );
            }
            return site.render(if mobile{"m.html"}else{"index.html"},json!({"request":{"path":path},"init_videos":long,"init_home_shorts":shorts,"init_mobile_video_cards":long,"today_schedules":schedules,"sidebar_members":site.members["sidebar_members"],"sidebar_generations":site.members["sidebar_generations"],"sidebar_ungrouped_top":site.members["sidebar_ungrouped_top"],"sidebar_ungrouped_bottom":site.members["sidebar_ungrouped_bottom"]}));
        }
        if let Some(id) = path.strip_prefix("/watch/") {
            let title = videos
                .iter()
                .find(|v| v["videoId"] == id)
                .and_then(|v| v["title"].as_str())
                .unwrap_or("루미너스");
            let template = if params.get("type").map(String::as_str) == Some("shorts") {
                "watch_shorts.html"
            } else if mobile {
                "watch_mobile.html"
            } else {
                "watch.html"
            };
            return site.render(template,json!({"request":{"path":path},"video_id":id,"is_drive":id.len()>=20,"page_title":format!("{title} - 루미너스"),"video_description":title}));
        }
        if path == "/credits" {
            let credits = std::fs::read(site.root.join("static/images/pngimg/credits.json"))
                .ok()
                .and_then(|v| serde_json::from_slice::<Value>(&v).ok())
                .unwrap_or(json!([]));
            return site.render(
                "credits.html",
                json!({"request":{"path":path},"credits":credits}),
            );
        }
        for (route, template) in [("/privacy", "privacy.html"), ("/terms", "terms.html")] {
            if path == route {
                return site.render(template, json!({"request":{"path":path}}));
            }
        }
        match path.as_str() {
            "/api/videos/home_shorts" | "/api/m/shorts" => return output(json!({"success":true,"videos":videos.iter().filter(|v|v["is_short"]==true).take(15).collect::<Vec<_>>()})),
            "/api/me" if user_id==0 => return output(json!({"logged_in":false,"discord_link_success":false,"discord_flash_error":null})),
            "/api/me" => {
                return match site.query("SELECT id,nickname,picture,email,discord_id,discord_username,password_hash,is_staff,staff_role,suspended FROM users WHERE id=?",&[json!(user_id)]).await {
                    Ok(users) if !users.is_empty() => {let u=&users[0];let nickname=u["nickname"].as_str().unwrap_or("");let role=env("STAFF_ROLE_LABELS","").split(',').find_map(|p|p.split_once(':').filter(|(nick,_)|nick.trim()==nickname).map(|(_,role)|role.trim().to_owned())).unwrap_or_else(||u["staff_role"].as_str().unwrap_or("").to_owned());let mut r=output(json!({"logged_in":true,"nickname":u["nickname"],"picture":u["picture"],"discord_linked":u["discord_id"].as_str().is_some_and(|s|!s.is_empty()),"discord_username":u["discord_username"],"needs_migration":u["password_hash"].as_str().is_none_or(|s|s.is_empty()),"needs_setup":session["needs_setup"]==true,"is_admin":auth::admin(u),"is_staff":u["is_staff"]==1||!role.is_empty(),"staff_role":role,"title":if role.is_empty(){"파스텔"}else{role.as_str()},"discord_link_success":session["flash_discord_linked"]==true,"discord_flash_error":session["flash_discord_error"]}));if session["flash_discord_linked"]!=Value::Null||session["flash_discord_error"]!=Value::Null{if let Some(o)=session.as_object_mut(){o.remove("flash_discord_linked");o.remove("flash_discord_error");o.remove("needs_setup");}r.headers_mut().insert("set-cookie",site.cookie(&session).parse().unwrap());}r},
                    Ok(_) => output(json!({"logged_in":false})),
                    Err(err) => {eprintln!("me: {err}");error(StatusCode::SERVICE_UNAVAILABLE,"회원 정보를 불러오지 못했습니다.")}
                }
            }
            "/logout" => {let mut r=axum::response::Redirect::to("/").into_response();r.headers_mut().insert("set-cookie",site.cookie(&json!({})).parse().unwrap());return r;}
            "/robots.txt" => return "User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin/\nSitemap: https://pastellive.co.kr/sitemap.xml\n".into_response(),
            _ => {}
        }
    }
    let headers = request.headers().clone();
    let content_type = headers
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    if !matches!(method.as_str(), "GET" | "HEAD" | "OPTIONS") && !trusted_origin(&headers) {
        return error(StatusCode::FORBIDDEN, "허용되지 않은 요청입니다.");
    }
    let raw = match to_bytes(request.into_body(), 8 * 1024 * 1024).await {
        Ok(b) => b,
        Err(_) => return error(StatusCode::PAYLOAD_TOO_LARGE, "요청이 너무 큽니다."),
    };
    if path == "/api/pubsub/callback" && method == axum::http::Method::POST {
        return misc::pubsub(&site, &headers, &raw);
    }
    let body = if content_type.starts_with("multipart/form-data") {
        if user_id == 0 {
            return error(StatusCode::UNAUTHORIZED, "로그인이 필요합니다.");
        }
        match uploads::parse(&site, &path, &content_type, raw).await {
            Ok(v) => v,
            Err(msg) => return error(StatusCode::BAD_REQUEST, &msg),
        }
    } else if content_type.starts_with("application/x-www-form-urlencoded") {
        let text = String::from_utf8_lossy(&raw);
        let uri = format!("/?{text}")
            .parse::<axum::http::Uri>()
            .unwrap_or_else(|_| "/".parse().unwrap());
        json!(query_params(&uri))
    } else if raw.is_empty() {
        json!({})
    } else {
        match serde_json::from_slice::<Value>(&raw) {
            Ok(value) => value,
            Err(_) => return error(StatusCode::BAD_REQUEST, "JSON 형식이 올바르지 않습니다."),
        }
    };
    let mut body = body;
    for key in ["content", "title", "description"] {
        if let Some(text) = body[key].as_str() {
            let text = ammonia::Builder::default()
                .tags(std::collections::HashSet::new())
                .clean(text)
                .to_string();
            body[key] = json!(text);
        }
    }
    if !matches!(method.as_str(), "GET" | "HEAD" | "OPTIONS") {
        let controls = site.controls.read().unwrap();
        let words = controls
            .settings
            .get("banned_words")
            .and_then(|s| serde_json::from_str::<Vec<String>>(s).ok())
            .unwrap_or_default();
        for key in ["content", "title", "description", "nickname"] {
            if let Some(text) = body[key].as_str() {
                let text = text.to_lowercase();
                if words
                    .iter()
                    .filter(|w| !w.is_empty())
                    .any(|w| text.contains(&w.to_lowercase()))
                {
                    return error(
                        StatusCode::BAD_REQUEST,
                        "사용할 수 없는 단어가 포함되어 있습니다.",
                    );
                }
            }
        }
    }
    match api(
        &site,
        &path,
        method.as_str(),
        &params,
        &headers,
        &session,
        &body,
    )
    .await
    {
        Ok(Some(response)) => response,
        Ok(None) => {
            if !is_admin && matches!(method.as_str(), "GET" | "HEAD" | "POST") {
                if let Some(ip) = headers
                    .get("cf-connecting-ip")
                    .and_then(|v| v.to_str().ok())
                    .and_then(|s| s.parse().ok())
                {
                    match site
                        .auto_ban(
                            ip,
                            &format!(
                                "자동 차단: 존재하지 않는 경로 {}",
                                path.chars().take(160).collect::<String>()
                            ),
                        )
                        .await
                    {
                        Ok(true) => {
                            return error(StatusCode::FORBIDDEN, "서버에서 IP가 차단되었습니다.");
                        }
                        Err(err) => eprintln!("없는 경로 IP 차단 저장 실패: {err}"),
                        _ => {}
                    }
                }
            }
            error(StatusCode::NOT_FOUND, "요청한 주소를 찾을 수 없습니다.")
        }
        Err(err) => {
            eprintln!("{path}: {err}");
            error(
                StatusCode::SERVICE_UNAVAILABLE,
                "요청을 처리하지 못했습니다.",
            )
        }
    }
}
async fn api(
    site: &Arc<Site>,
    path: &str,
    method: &str,
    params: &HashMap<String, String>,
    headers: &HeaderMap,
    session: &Value,
    body: &Value,
) -> Result<Option<Response>, sqlx::Error> {
    if !matches!(method, "GET" | "HEAD" | "OPTIONS") && headers.get("cookie").is_some() {
        let origin = headers
            .get("origin")
            .or_else(|| headers.get("referer"))
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        let host = reqwest::Url::parse(origin)
            .ok()
            .and_then(|u| u.host_str().map(str::to_string))
            .unwrap_or_default();
        let site_host = env("SITE_HOST", "pastellive.co.kr");
        if host != site_host
            && host != format!("m.{site_host}")
            && host != env("ADMIN_HOSTNAME", "admin.pastellive.co.kr")
            && host != "127.0.0.1"
            && host != "localhost"
        {
            return Ok(Some(error(
                StatusCode::FORBIDDEN,
                "허용되지 않은 요청입니다.",
            )));
        }
    }
    if path.starts_with("/api/lumi/") {
        return Ok(Some(lumi::api(site, path, method, headers, session, body).await));
    }    if let Some(response) = auth::api(site, path, method, headers, session, body).await? {
        return Ok(Some(response));
    }
    if let Some(response) =
        personal::api(site, path, method, params, headers, session, body).await?
    {
        return Ok(Some(response));
    }
    if let Some(response) = social::api(site, path, method, params, headers, session, body).await? {
        return Ok(Some(response));
    }
    if let Some(response) = public::api(site, path, method, params, headers, session, body).await? {
        return Ok(Some(response));
    }
    if let Some(response) = account::api(site, path, method, params, headers, session, body).await?
    {
        return Ok(Some(response));
    }
    if let Some(response) =
        upstream::api(site, path, method, params, headers, session, body).await?
    {
        return Ok(Some(response));
    }
    if let Some(response) = admin::api(site, path, method, params, headers, session, body).await? {
        return Ok(Some(response));
    }
    misc::api(site, path, method, params, headers, session, body).await
}
fn trusted_origin(headers: &HeaderMap) -> bool {
    if headers.get("cookie").is_none() {
        return true;
    }
    let origin = headers
        .get("origin")
        .or_else(|| headers.get("referer"))
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let host = reqwest::Url::parse(origin)
        .ok()
        .and_then(|u| u.host_str().map(str::to_string))
        .unwrap_or_default();
    let root = env("SITE_HOST", "pastellive.co.kr");
    [
        root.clone(),
        format!("m.{root}"),
        env("ADMIN_HOSTNAME", "admin.pastellive.co.kr"),
        "127.0.0.1".into(),
        "localhost".into(),
    ]
    .contains(&host)
}

fn contains_lumi_fword(message: &str, words: &str) -> bool {
    let message = message.to_lowercase();
    words.lines().map(|word| word.trim().trim_start_matches('\u{feff}').to_lowercase())
        .any(|word| !word.is_empty() && message.contains(&word))
}

#[cfg(test)]
mod lumi_filter_tests {
    use super::contains_lumi_fword;
    #[test]
    fn matches_contained_words_case_insensitively() {
        let words = "\u{feff}나쁜말\r\nBADWORD\n\n";
        assert!(contains_lumi_fword("앞 나쁜말 뒤", words));
        assert!(contains_lumi_fword("a badWORD here", words));
        assert!(!contains_lumi_fword("안녕 루미", words));
        assert!(!contains_lumi_fword("정상 메시지", "\n \n"));
    }
}
fn lumi_casual_reply(reply: &str) -> String {
    let mut text = reply.to_string();
    for (formal, casual) in [
        ("하자요", "하자"), ("되자요", "되자"), ("좋아요", "좋아"),
        ("주세요", "줘"), ("줘요", "줘"), ("해요", "해"), ("돼요", "돼"),
        ("되요", "돼"), ("봐요", "봐"), ("가요", "가"), ("나요", "나"),
        ("있어요", "있어"), ("없어요", "없어"), ("이에요", "이야"),
        ("예요", "야"), ("거에요", "거야"), ("거예요", "거야"),
        ("게요", "게"), ("네요", "네"), ("죠", "지"), ("까요", "까"),
        ("잖아요", "잖아"), ("거든요", "거든"), ("괜찮아요", "괜찮아"),
        ("고마워요", "고마워"), ("반가워요", "반가워"), ("싶어요", "싶어"),
        ("알겠어요", "알겠어"), ("맞아요", "맞아"), ("아니에요", "아니야"),
        ("입니다", "이야"), ("합니다", "해"), ("드립니다", "줄게"),
    ] { text = text.replace(formal, casual); }
    text
}

#[cfg(test)]
mod lumi_tone_tests {
    #[test]
    fn repairs_mixed_and_malformed_endings() {
        assert_eq!(super::lumi_casual_reply("좋아요! 친구가 되자요. 이야기하자요. 말해줘요!"),
            "좋아! 친구가 되자. 이야기하자. 말해줘!");
        assert_eq!(super::lumi_casual_reply("여기는 루미너스야!"), "여기는 루미너스야!");
    }
}
