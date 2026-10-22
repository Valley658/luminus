use super::*;
use rand::seq::SliceRandom;
fn number(v: &Value) -> i64 {
    v.as_i64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .unwrap_or(0)
}
fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 100
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}
pub fn pubsub(site: &Site, headers: &HeaderMap, body: &[u8]) -> Response {
    use subtle::ConstantTimeEq;
    let secret = env("WEBSUB_SECRET", "");
    if secret.is_empty() {
        return StatusCode::FORBIDDEN.into_response();
    }
    let mut mac = Hmac::<sha1::Sha1>::new_from_slice(secret.as_bytes()).unwrap();
    mac.update(body);
    let expected = format!(
        "sha1={}",
        mac.finalize()
            .into_bytes()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    );
    let supplied = headers
        .get("x-hub-signature")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let _ = site;
    if bool::from(expected.as_bytes().ct_eq(supplied.as_bytes())) {
        StatusCode::NO_CONTENT.into_response()
    } else {
        StatusCode::FORBIDDEN.into_response()
    }
}
async fn delete_user(site: &Site, id: i64) -> Result<(), sqlx::Error> {
    let mut tx = site.db.begin().await?;
    for sql in [
        "DELETE pv FROM user_playlist_videos pv JOIN user_playlists p ON pv.playlist_id=p.playlist_id WHERE p.user_id=?",
        "DELETE FROM watch_history WHERE user_id=?",
        "DELETE FROM user_bookmarks WHERE user_id=?",
        "DELETE FROM user_playlists WHERE user_id=?",
        "DELETE FROM comment_likes WHERE user_id=?",
        "DELETE FROM video_comments WHERE user_id=?",
        "DELETE FROM fanart_reactions WHERE user_id=?",
        "DELETE FROM community_likes WHERE user_id=?",
        "DELETE FROM user_attendance WHERE user_id=?",
        "DELETE FROM channel_subscriptions WHERE subscriber_user_id=?",
        "DELETE FROM user_login_log WHERE user_id=?",
        "UPDATE fanart_gallery SET nickname='탈퇴한 사용자',ip_address=NULL WHERE user_id=?",
        "UPDATE fanart_comments SET nickname='탈퇴한 사용자',picture=NULL,ip_address=NULL WHERE user_id=?",
        "UPDATE fanart_reports SET reporter_nickname='탈퇴한 사용자',ip_address=NULL WHERE reporter_user_id=?",
        "UPDATE community_posts SET nickname='탈퇴한 사용자',picture=NULL,ip_address=NULL WHERE user_id=?",
        "UPDATE community_comments SET nickname='탈퇴한 사용자',picture=NULL,ip_address=NULL WHERE user_id=?",
        "DELETE FROM users WHERE id=?",
    ] {
        sqlx::query(sql).bind(id).execute(&mut *tx).await?;
    }
    tx.commit().await
}
pub async fn api(
    site: &Site,
    path: &str,
    method: &str,
    params: &HashMap<String, String>,
    headers: &HeaderMap,
    session: &Value,
    body: &Value,
) -> Result<Option<Response>, sqlx::Error> {
    let uid = session["user_id"].as_i64().unwrap_or(0);
    let parts: Vec<_> = path.trim_matches('/').split('/').collect();
    if method == "GET" {
        match path {
            "/api/live_status" => {
                return Ok(Some(output(site.live.read().unwrap()["status"].clone())));
            }
            "/api/kirinuki/videos" => {
                return Ok(Some(output(
                    json!({"success":true,"disabled":true,"videos":[],"nextPageToken":null}),
                )));
            }
            "/api/pubsub/callback" => {
                return Ok(Some(
                    if matches!(
                        params.get("hub.mode").map(String::as_str),
                        Some("subscribe" | "unsubscribe")
                    ) {
                        params
                            .get("hub.challenge")
                            .cloned()
                            .unwrap_or_default()
                            .into_response()
                    } else {
                        StatusCode::NOT_FOUND.into_response()
                    },
                ));
            }
            "/highlights" => return Ok(Some(site.render(
                "highlights.html",
                json!({"request":{"path":path},"sidebar_members":site.members["sidebar_members"]}),
            ))),
            "/member" => {
                let name = params
                    .get("name")
                    .or_else(|| params.get("member"))
                    .map(String::as_str)
                    .unwrap_or("");
                if headers
                    .get("x-requested-with")
                    .and_then(|v| v.to_str().ok())
                    != Some("LuminousXHR")
                    || site.members["MEMBER_FULL_NAMES"][name].is_null()
                {
                    return Ok(Some(error(
                        StatusCode::BAD_REQUEST,
                        "올바른 멤버를 선택해주세요.",
                    )));
                }
                return Ok(Some(output(
                    json!({"success":true,"redirect":format!("/?view_member={name}")}),
                )));
            }
            "/sw.js" => {
                let mut r = match std::fs::read(site.root.join("static/sw.js")) {
                    Ok(data) => data.into_response(),
                    Err(_) => StatusCode::NOT_FOUND.into_response(),
                };
                r.headers_mut().insert(
                    "content-type",
                    "application/javascript; charset=utf-8".parse().unwrap(),
                );
                r.headers_mut()
                    .insert("service-worker-allowed", "/".parse().unwrap());
                r.headers_mut()
                    .insert("cache-control", "no-cache".parse().unwrap());
                return Ok(Some(r));
            }
            "/favicon.ico" => {
                return Ok(Some(
                    axum::response::Redirect::to("/static/favicon.ico").into_response(),
                ));
            }
            "/sitemap.xml" => {
                let root = format!("https://{}", env("SITE_HOST", "pastellive.co.kr"));
                let mut xml = String::from(
                    "<?xml version=\"1.0\" encoding=\"UTF-8\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">",
                );
                for p in ["/", "/gallery", "/highlights"] {
                    xml.push_str(&format!("<url><loc>{root}{p}</loc></url>"));
                }
                for v in site.videos.load().iter() {
                    if let Some(id) = v["videoId"].as_str().filter(|id| valid_id(id)) {
                        xml.push_str(&format!("<url><loc>{root}/watch/{id}</loc></url>"));
                    }
                }
                xml.push_str("</urlset>");
                return Ok(Some(
                    (
                        [
                            ("content-type", "application/xml; charset=utf-8"),
                            ("cache-control", "public, max-age=3600"),
                        ],
                        xml,
                    )
                        .into_response(),
                ));
            }
            "/api/highlights" => {
                let limit = params
                    .get("limit")
                    .and_then(|v| v.parse::<i64>().ok())
                    .unwrap_or(30)
                    .clamp(1, 60);
                let offset = params
                    .get("offset")
                    .and_then(|v| v.parse::<i64>().ok())
                    .unwrap_or(0)
                    .clamp(0, 10000);
                let member = params.get("member").cloned().unwrap_or_default();
                return Ok(Some(output(
                    json!({"success":true,"clips":site.query("SELECT id,source_video_id,source_title,member_name,start_seconds,duration_seconds,tag_text,clip_path,gif_path,sprite_path,sprite_frame_count,sprite_frame_width,sprite_interval_seconds,nickname,view_count,created_at FROM highlight_clips WHERE status='ready' AND (?='' OR member_name=?) ORDER BY id DESC LIMIT ? OFFSET ?",&[json!(member),json!(member),json!(limit),json!(offset)]).await?}),
                )));
            }
            _ => {}
        }
        if let Some(name) = path.strip_prefix("/api/live_detail/") {
            let name = percent_encoding::percent_decode_str(name).decode_utf8_lossy();
            let value = site.live.read().unwrap()["details"][name.as_ref()].clone();
            return Ok(Some(output(if value.is_null() {
                json!({"success":false})
            } else {
                json!({"success":true,"live":value})
            })));
        }
        if parts.len() == 3 && parts[1] == "highlights" {
            let id = parts[2].parse::<i64>().unwrap_or(0);
            let rows=site.query("SELECT id,source_video_id,source_title,member_name,start_seconds,duration_seconds,tag_text,status,clip_path,gif_path,sprite_path,sprite_frame_count,sprite_frame_width,sprite_interval_seconds,error_message,nickname FROM highlight_clips WHERE id=?",&[json!(id)]).await?;
            return Ok(Some(match rows.first() {
                Some(clip) => output(json!({"success":true,"clip":clip})),
                None => error(StatusCode::NOT_FOUND, "존재하지 않는 클립입니다."),
            }));
        }
        if let Some(id) = path.strip_prefix("/api/drive_video_stream/") {
            if !valid_id(id) {
                return Ok(Some(error(
                    StatusCode::BAD_REQUEST,
                    "잘못된 파일 ID입니다.",
                )));
            }
            let key = env("GOOGLE_DRIVE_API_KEY", "");
            if key.is_empty() {
                return Ok(Some(error(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "영상 API가 설정되지 않았습니다.",
                )));
            }
            let permit = match super::upstream::UPSTREAM.try_acquire() {
                Ok(p) => p,
                Err(_) => {
                    return Ok(Some(error(
                        StatusCode::TOO_MANY_REQUESTS,
                        "잠시 후 다시 시도해주세요.",
                    )));
                }
            };
            let mut req = site
                .client
                .get(format!("https://www.googleapis.com/drive/v3/files/{id}"))
                .query(&[("alt", "media"), ("key", key.as_str())]);
            if let Some(range) = headers.get("range") {
                req = req.header("range", range);
            }
            let upstream = match req.send().await {
                Ok(r) if r.status().is_success() => r,
                _ => {
                    return Ok(Some(error(
                        StatusCode::BAD_GATEWAY,
                        "영상을 불러오지 못했습니다.",
                    )));
                }
            };
            let status = upstream.status();
            let upstream_headers = upstream.headers().clone();
            let ct = upstream_headers
                .get("content-type")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_owned();
            let media = ct.starts_with("video/") || ct.starts_with("audio/");
            use futures_util::StreamExt;
            let mut stream = upstream.bytes_stream();
            let stream = async_stream::stream! {let _permit=permit;while let Some(chunk)=stream.next().await{yield chunk;}};
            let mut r = Response::new(axum::body::Body::from_stream(stream));
            *r.status_mut() = status;
            for h in ["content-length", "content-range", "accept-ranges"] {
                if let Some(value) = upstream_headers.get(h) {
                    r.headers_mut().insert(h, value.clone());
                }
            }
            r.headers_mut().insert(
                "content-type",
                if media {
                    ct.as_str()
                } else {
                    "application/octet-stream"
                }
                .parse()
                .unwrap(),
            );
            if !media {
                r.headers_mut()
                    .insert("content-disposition", "attachment".parse().unwrap());
            }
            r.headers_mut().insert(
                "content-security-policy",
                "default-src 'none'; sandbox".parse().unwrap(),
            );
            r.headers_mut()
                .insert("x-content-type-options", "nosniff".parse().unwrap());
            return Ok(Some(r));
        }
        if parts.len() == 3 && parts[0] == "share" {
            let kind = parts[1];
            let id = parts[2];
            if !valid_id(id) {
                return Ok(Some(StatusCode::NOT_FOUND.into_response()));
            }
            let root = format!("https://{}", env("SITE_HOST", "pastellive.co.kr"));
            let mut title = "루미너스".to_owned();
            let mut description = "루미너스에서 함께 즐겨보세요.".to_owned();
            let mut image = format!("{root}/static/logo/logo.png");
            let target;
            if kind == "fanart" {
                let rows=site.query("SELECT title,description,image_url FROM fanart_gallery WHERE id=? AND status='active'",&[json!(id)]).await?;
                if let Some(row) = rows.first() {
                    title = row["title"].as_str().unwrap_or(&title).to_owned();
                    description = row["description"]
                        .as_str()
                        .unwrap_or(&description)
                        .to_owned();
                    if let Some(url) = row["image_url"].as_str() {
                        image = if url.starts_with('/') {
                            format!("{root}{url}")
                        } else {
                            url.into()
                        };
                    }
                }
                target = format!("/?image={id}");
            } else if kind == "video" {
                if let Some(v) = site.videos.load().iter().find(|v| v["videoId"] == id) {
                    title = v["title"].as_str().unwrap_or(&title).into();
                    image = v["thumbnail"].as_str().unwrap_or(&image).into();
                }
                target = format!("/watch/{id}?from=share");
            } else {
                return Ok(Some(StatusCode::NOT_FOUND.into_response()));
            }
            let ua = headers
                .get("user-agent")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_lowercase();
            if !["bot", "kakaotalk", "whatsapp", "slack"]
                .iter()
                .any(|b| ua.contains(b))
            {
                return Ok(Some(axum::response::Redirect::to(&target).into_response()));
            }
            return Ok(Some(site.render("share.html",json!({"title":title,"description":description,"image":image,"redirect_target":target,"site_url":root,"share_url":format!("{root}{path}")}))));
        }
    }
    if method == "POST" && path == "/api/csp-report" {
        return Ok(Some(StatusCode::NO_CONTENT.into_response()));
    }
    if matches!(method, "POST" | "GET")
        && matches!(
            path,
            "/api/videos/random_scroll" | "/api/videos/recommend_random"
        )
    {
        let exclusions: std::collections::HashSet<String> = if method == "POST" {
            body["exclude"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .take(4000)
                        .filter_map(Value::as_str)
                        .map(str::to_owned)
                        .collect()
                })
                .unwrap_or_default()
        } else {
            params
                .get("exclude")
                .map(|s| s.split(',').take(4000).map(str::to_owned).collect())
                .unwrap_or_default()
        };
        let videos = site.videos.load_full();
        let current = params.get("v").map(String::as_str).unwrap_or("");
        let mut candidates: Vec<_> = videos
            .iter()
            .filter(|v| {
                v["is_short"] != true
                    && !exclusions.contains(v["videoId"].as_str().unwrap_or(""))
                    && v["videoId"] != current
            })
            .collect();
        candidates.shuffle(&mut rand::thread_rng());
        let remaining = candidates.len();
        candidates.truncate(12);
        if path == "/api/videos/recommend_random" {
            let mut shorts: Vec<_> = videos.iter().filter(|v| v["is_short"] == true
                && !exclusions.contains(v["videoId"].as_str().unwrap_or(""))
                && v["videoId"] != current).collect();
            shorts.shuffle(&mut rand::thread_rng());
            shorts.truncate(8);
            candidates.extend(shorts);
        }
        return Ok(Some(output(
            json!({"videos":candidates,"remaining":remaining,"nextPageToken":if remaining>12{Some("has_more_random")}else{None}}),
        )));
    }
    if method == "POST" && path == "/api/highlights/tag" {
        if uid == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        let id = body["videoId"].as_str().unwrap_or("");
        let start = number(&body["startSeconds"]);
        let tag = body["tag"].as_str().unwrap_or("").trim();
        if id.len() != 11
            || !valid_id(id)
            || !(0..=86400).contains(&start)
            || tag.is_empty()
            || tag.chars().count() > 200
        {
            return Ok(Some(error(
                StatusCode::BAD_REQUEST,
                "영상 ID, 시작 시간과 태그를 확인해주세요.",
            )));
        }
        let videos = site.videos.load_full();
        let metadata = videos
            .iter()
            .find(|v| v["videoId"] == id)
            .cloned()
            .unwrap_or(json!({}));
        let duration = body["durationSeconds"].as_i64().unwrap_or(15).clamp(5, 30);
        let ip = headers
            .get("cf-connecting-ip")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("127.0.0.1");
        let clip=site.execute("INSERT INTO highlight_clips (source_video_id,source_title,member_name,start_seconds,duration_seconds,tag_text,status,ip_address,user_id,nickname) VALUES (?,?,?,?,?,?,'pending',?,?,?)",&[json!(id),metadata["title"].clone(),metadata["member_name"].clone(),json!(start),json!(duration),json!(tag),json!(ip),json!(uid),session["user_nickname"].clone()]).await?;
        return Ok(Some(output(
            json!({"success":true,"clipId":clip,"status":"pending"}),
        )));
    }
    if method == "POST" && path == "/api/me/withdraw" {
        if uid == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        delete_user(site, uid).await?;
        let mut r = output(json!({"success":true,"message":"회원 탈퇴가 완료되었습니다."}));
        r.headers_mut()
            .insert("set-cookie", site.cookie(&json!({})).parse().unwrap());
        return Ok(Some(r));
    }
    let admin_route = path.starts_with("/api/admin/")
        || path.starts_with("/api/reports/")
        || path.starts_with("/api/schedules");
    if admin_route {
        let users = site
            .query(
                "SELECT id,nickname,email FROM users WHERE id=?",
                &[json!(uid)],
            )
            .await?;
        if !users.first().is_some_and(super::auth::admin) {
            return Ok(Some(error(StatusCode::FORBIDDEN, "권한이 없습니다.")));
        }
        if method == "DELETE" && parts.len() == 4 && parts[2] == "users" {
            let id = parts[3].parse::<i64>().unwrap_or(0);
            if id == uid || id <= 0 {
                return Ok(Some(error(
                    StatusCode::BAD_REQUEST,
                    "이 회원은 삭제할 수 없습니다.",
                )));
            }
            delete_user(site, id).await?;
            return Ok(Some(output(json!({"success":true}))));
        }
        if method == "POST" && parts.len() == 4 && parts[1] == "reports" && parts[3] == "resolve" {
            site.execute(
                "UPDATE fanart_reports SET status='resolved' WHERE id=?",
                &[json!(parts[2])],
            )
            .await?;
            return Ok(Some(output(json!({"success":true}))));
        }
        if path == "/api/admin/system" && method == "GET" {
            let cpu_usage = tokio::task::spawn_blocking(crate::supervisor::cpu_usage_percent).await.ok().flatten();
            let hardware = std::fs::read(site.root.join("logs/hardware.json")).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()).unwrap_or(json!({}));
            let (disk_total, disk_free) = crate::supervisor::disk_bytes(&site.root);
            let (memory_total, memory_available) = crate::supervisor::memory_bytes();
            let started = std::time::Instant::now();
            site.query("SELECT 1", &[]).await?;
            let (cpu, memory) = crate::supervisor::job_limits();
            let newest = std::fs::read_dir(site.root.join("backups/db"))
                .ok()
                .into_iter()
                .flatten()
                .filter_map(Result::ok)
                .filter(|e| e.file_name().to_string_lossy().starts_with("luminus_db_"))
                .filter_map(|e| e.metadata().ok()?.modified().ok())
                .max();
            let backup_at = newest
                .map(|t| chrono::DateTime::<chrono::Utc>::from(t).to_rfc3339())
                .unwrap_or_default();
            let backup_age = newest
                .and_then(|t| t.elapsed().ok())
                .map(|d| d.as_secs_f64() / 3600.0)
                .unwrap_or(-1.0);
            let mut response = output(
                json!({"success":true,"sampled_at":chrono::Utc::now().to_rfc3339(),"checks":[{"name":"데이터베이스","status":"up","detail":"정상","latency_ms":started.elapsed().as_millis()}],"cpu_usage_percent":cpu_usage,"hardware":hardware,"memory_total_bytes":memory_total,"memory_available_bytes":memory_available,"memory_used_percent":100.0*(1.0-memory_available as f64/memory_total.max(1) as f64),"disk_total_bytes":disk_total,"disk_free_bytes":disk_free,"disk_used_percent":100.0*(1.0-disk_free as f64/disk_total.max(1) as f64),"backup_at":backup_at,"backup_age_hours":backup_age,"alerts":[],"language":"Rust","cpu_limit_percent":cpu,"memory_limit_mb":memory,"db_pool_max":4,"threads":2}),
            );
            response.headers_mut().insert("cache-control", "no-store".parse().unwrap());
            return Ok(Some(response));
        }
        if path == "/api/schedules" && method == "POST"
            || parts.len() == 3 && parts[1] == "schedules" && matches!(method, "PUT" | "DELETE")
        {
            let id = parts
                .get(2)
                .and_then(|s| s.parse::<i64>().ok())
                .unwrap_or(0);
            if method == "DELETE" {
                site.execute("DELETE FROM member_schedules WHERE id=?", &[json!(id)])
                    .await?;
                return Ok(Some(output(json!({"success":true}))));
            }
            let member = body["member_name"].as_str().unwrap_or("");
            let date = body["event_date"].as_str().unwrap_or("");
            let time = body["event_time"].as_str().unwrap_or("");
            let day_off = body["is_day_off"] == true;
            let title = body["title"]
                .as_str()
                .unwrap_or(if day_off { "휴방" } else { "" });
            if site.members["MEMBER_FULL_NAMES"][member].is_null()
                || chrono::NaiveDate::parse_from_str(date, "%Y-%m-%d").is_err()
                || !time.is_empty() && chrono::NaiveTime::parse_from_str(time, "%H:%M").is_err()
                || title.is_empty()
                || title.chars().count() > 255
            {
                return Ok(Some(error(
                    StatusCode::BAD_REQUEST,
                    "멤버, 날짜, 시간과 제목을 확인해주세요.",
                )));
            }
            let time = if time.is_empty() {
                Value::Null
            } else {
                json!(time)
            };
            if method == "POST" {
                let id=site.execute("INSERT INTO member_schedules (member_name,event_date,event_time,title,is_day_off,created_by,updated_by) VALUES (?,?,?,?,?,?,?)",&[json!(member),json!(date),time,json!(title),json!(day_off),json!(uid),json!(uid)]).await?;
                return Ok(Some(output(json!({"success":true,"id":id}))));
            }
            site.execute("UPDATE member_schedules SET member_name=?,event_date=?,event_time=?,title=?,is_day_off=?,updated_by=? WHERE id=?",&[json!(member),json!(date),time,json!(title),json!(day_off),json!(uid),json!(id)]).await?;
            return Ok(Some(output(json!({"success":true}))));
        }
    }
    Ok(None)
}
