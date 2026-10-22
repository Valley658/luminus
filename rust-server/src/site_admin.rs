use super::*;
fn int(v: &Value) -> i64 {
    v.as_i64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .unwrap_or(0)
}
async fn scalar(site: &Site, sql: &str, args: &[Value]) -> Result<Value, sqlx::Error> {
    Ok(site
        .query(sql, args)
        .await?
        .into_iter()
        .next()
        .and_then(|v| v.as_object().and_then(|o| o.values().next()).cloned())
        .unwrap_or(json!(0)))
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
    let required = path.starts_with("/api/admin/")
        || ["/api/all_comments", "/api/delete_comments", "/api/reports"].contains(&path)
        || path.starts_with("/api/reports/")
        || path == "/api/search/trending/delete"
        || path == "/api/schedules" && method != "GET"
        || path.starts_with("/api/schedules/");
    if !required {
        return Ok(None);
    }
    let uid = session["user_id"].as_i64().unwrap_or(0);
    let users = site
        .query(
            "SELECT id,nickname,email FROM users WHERE id=?",
            &[json!(uid)],
        )
        .await?;
    let Some(user) = users.first().filter(|u| super::auth::admin(u)) else {
        return Ok(Some(error(StatusCode::FORBIDDEN, "권한이 없습니다.")));
    };
    if path == "/api/admin/lumi" || path.starts_with("/api/admin/lumi/") {
        return Ok(Some(super::lumi::admin_api(site, path, method, body).await));
    }
    let page = params
        .get("page")
        .and_then(|p| p.parse::<i64>().ok())
        .unwrap_or(1)
        .clamp(1, 10000);
    let per_page = params
        .get("per_page")
        .and_then(|p| p.parse::<i64>().ok())
        .unwrap_or(30)
        .clamp(1, 100);
    let offset = (page - 1) * per_page;
    let q = params
        .get("q")
        .cloned()
        .unwrap_or_default()
        .chars()
        .take(100)
        .collect::<String>();
    let like = json!(format!("%{q}%"));
    let parts: Vec<_> = path.trim_matches('/').split('/').collect();
    let mut result = None;
    match (method, path) {
        ("GET", "/api/admin/users") => {
            let filter = match params.get("filter").map(String::as_str) {
                Some("staff") => " AND is_staff=1",
                Some("suspended") => " AND suspended=1",
                _ => "",
            };
            let order = match params.get("sort").map(String::as_str) {
                Some("attendance") => "total_attendance DESC,id DESC",
                Some("oldest") => "id ASC",
                _ => "id DESC",
            };
            let users=site.query(&format!("SELECT id,email,nickname,picture,login_id,last_ip,created_at,updated_at,channel_id,discord_id,discord_username,total_attendance,consecutive_attendance,is_staff,staff_role,suspended,suspended_reason FROM users WHERE (nickname LIKE ? OR login_id LIKE ? OR email LIKE ?){filter} ORDER BY {order} LIMIT ? OFFSET ?"),&[like.clone(),like.clone(),like.clone(),json!(per_page),json!(offset)]).await?;
            let total=scalar(site,&format!("SELECT COUNT(*) FROM users WHERE (nickname LIKE ? OR login_id LIKE ? OR email LIKE ?){filter}"),&[like.clone(),like.clone(),like.clone()]).await?;
            result = Some(
                json!({"success":true,"users":users,"total":total,"page":page,"per_page":per_page}),
            );
        }
        ("POST", "/api/admin/audit-log/reset") => {
            site.execute("DELETE FROM admin_audit_log", &[]).await?;
            result = Some(json!({"success":true}));
        }
        ("GET", "/api/admin/audit-log") => {
            result = Some(
                json!({"success":true,"logs":site.query("SELECT id,admin_email,admin_nickname,action,target_type,target_id,detail,ip_address,created_at AS date FROM admin_audit_log ORDER BY id DESC LIMIT 200",&[]).await?}),
            )
        }
        ("GET", "/api/admin/search-trends") => {
            result = Some(
                json!({"success":true,"trends":site.query("SELECT keyword,search_count,last_searched FROM search_trends WHERE keyword LIKE ? ORDER BY search_count DESC LIMIT 300",&[like.clone()]).await?}),
            )
        }
        ("GET", "/api/admin/blocked-paths") => {
            result = Some(
                json!({"success":true,"paths":site.query("SELECT path,blocked_at FROM blocked_paths ORDER BY blocked_at DESC",&[]).await?}),
            )
        }
        ("POST", "/api/admin/blocked-paths") | ("POST", "/api/admin/blocked-paths/delete") => {
            let path = body["path"].as_str().unwrap_or("");
            if !path.starts_with('/')
                || path == "/"
                || path.len() > 255
                || path.starts_with("/api/admin")
            {
                return Ok(Some(error(
                    StatusCode::BAD_REQUEST,
                    "차단할 경로를 확인해주세요.",
                )));
            }
            site.execute(
                if parts.last() == Some(&"delete") {
                    "DELETE FROM blocked_paths WHERE path=?"
                } else {
                    "INSERT IGNORE INTO blocked_paths (path) VALUES (?)"
                },
                &[json!(path)],
            )
            .await?;
            site.refresh_controls().await?;
            result = Some(json!({"success":true}));
        }
        ("GET", "/api/admin/site-settings") => {
            let controls = site.controls.read().unwrap();
            let mut settings = json!({});
            for (key, value) in &controls.settings {
                settings[key] = if key.ends_with("_enabled") {
                    json!(value == "1")
                } else if key == "banned_words" {
                    serde_json::from_str(value).unwrap_or(json!([]))
                } else {
                    json!(value)
                };
            }
            result = Some(json!({"success":true,"settings":settings}));
        }
        ("POST", "/api/admin/site-settings") => {
            if let Some(object) = body.as_object() {
                for (key, value) in object {
                    if ![
                        "notice_enabled",
                        "notice_text",
                        "notice_level",
                        "notice_link",
                        "maintenance_enabled",
                        "maintenance_message",
                        "banned_words",
                    ]
                    .contains(&key.as_str())
                    {
                        continue;
                    }
                    let val = if let Some(b) = value.as_bool() {
                        if b { "1" } else { "0" }.to_string()
                    } else if let Some(s) = value.as_str() {
                        s.to_string()
                    } else {
                        value.to_string()
                    };
                    if val.len() > 20000 {
                        return Ok(Some(error(
                            StatusCode::BAD_REQUEST,
                            "설정 내용이 너무 큽니다.",
                        )));
                    }
                    site.execute("INSERT INTO site_settings (setting_key,setting_value) VALUES (?,?) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)",&[json!(key),json!(val)]).await?;
                }
            }
            if body
                .as_object()
                .is_some_and(|o| o.keys().any(|k| k.starts_with("notice_")))
            {
                let revision = format!(
                    "{}-{}",
                    chrono::Utc::now().timestamp_millis(),
                    rand::random::<u64>()
                );
                site.execute("INSERT INTO site_settings (setting_key,setting_value) VALUES ('notice_revision',?) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)",&[json!(revision)]).await?;
            }
            site.refresh_controls().await?;
            result = Some(json!({"success":true}));
        }
        ("POST", "/api/admin/search-trends/delete") | ("POST", "/api/search/trending/delete") => {
            site.execute(
                "DELETE FROM search_trends WHERE keyword=?",
                &[body["keyword"].clone()],
            )
            .await?;
            result = Some(json!({"success":true}));
        }
        ("POST", "/api/admin/search-trends/delete-all") => {
            site.execute("DELETE FROM search_trends", &[]).await?;
            result = Some(json!({"success":true}));
        }
        ("GET", "/api/admin/fanarts") | ("GET", "/api/admin/posts") => {
            let (table, key, columns) = if path.ends_with("fanarts") {
                (
                    "fanart_gallery",
                    "fanarts",
                    "id,user_id,nickname,title,description,image_url,thumbnail_url,status,created_at",
                )
            } else {
                (
                    "community_posts",
                    "posts",
                    "id,user_id,nickname,title,content,member_name,image_url,video_url,created_at",
                )
            };
            let counters = if table == "fanart_gallery" {
                ",(SELECT COUNT(*) FROM fanart_reactions r WHERE r.fanart_id=fanart_gallery.id AND reaction_type='like') AS likes,(SELECT COUNT(*) FROM fanart_reactions r WHERE r.fanart_id=fanart_gallery.id AND reaction_type='dislike') AS dislikes,(SELECT COUNT(*) FROM fanart_reactions r WHERE r.fanart_id=fanart_gallery.id AND reaction_type='report') AS reports,(SELECT COUNT(*) FROM fanart_comments c WHERE c.fanart_id=fanart_gallery.id) AS comments"
            } else {
                ",(SELECT COUNT(*) FROM community_likes l WHERE target_type='post' AND target_id=community_posts.id AND reaction='like') AS likes,(SELECT COUNT(*) FROM community_comments c WHERE c.post_id=community_posts.id) AS comments"
            };
            let rows=site.query(&format!("SELECT {columns}{counters} FROM {table} WHERE nickname LIKE ? OR title LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?"),&[like.clone(),like.clone(),json!(per_page),json!(offset)]).await?;
            let total = scalar(
                site,
                &format!("SELECT COUNT(*) FROM {table} WHERE nickname LIKE ? OR title LIKE ?"),
                &[like.clone(), like.clone()],
            )
            .await?;
            let mut data = json!({"success":true,"total":total,"page":page,"per_page":per_page});
            data[key] = json!(rows);
            result = Some(data);
        }
        ("POST", "/api/admin/fanarts/delete") | ("POST", "/api/admin/posts/delete") => {
            let mut deleted = 0;
            if let Some(ids) = body["ids"].as_array() {
                for id in ids.iter().take(500) {
                    let id = int(id);
                    if id < 1 {
                        continue;
                    }
                    if path.contains("fanarts") {
                        super::social::delete_fanart(site, id).await?;
                        deleted += 1;
                        continue;
                    }
                    let mut tx = site.db.begin().await?;
                    if path.contains("fanarts") {
                        for table in ["fanart_comments", "fanart_reactions", "fanart_reports"] {
                            sqlx::query(&format!("DELETE FROM {table} WHERE fanart_id=?"))
                                .bind(id)
                                .execute(&mut *tx)
                                .await?;
                        }
                        sqlx::query("DELETE FROM fanart_gallery WHERE id=?")
                            .bind(id)
                            .execute(&mut *tx)
                            .await?;
                    } else {
                        sqlx::query("DELETE FROM community_comments WHERE post_id=?")
                            .bind(id)
                            .execute(&mut *tx)
                            .await?;
                        sqlx::query(
                            "DELETE FROM community_likes WHERE target_type='post' AND target_id=?",
                        )
                        .bind(id.to_string())
                        .execute(&mut *tx)
                        .await?;
                        sqlx::query("DELETE FROM community_posts WHERE id=?")
                            .bind(id)
                            .execute(&mut *tx)
                            .await?;
                    }
                    tx.commit().await?;
                    deleted += 1;
                }
            }
            result = Some(json!({"success":true,"deleted":deleted}));
        }
        ("GET", "/api/all_comments") => {
            result = Some(
                json!({"success":true,"comments":site.query("SELECT id,video_id,user_id,nickname,content,image_url,ip_address,created_at AS date FROM video_comments ORDER BY id DESC LIMIT 500",&[]).await?}),
            )
        }
        ("POST", "/api/delete_comments") => {
            if let Some(ids) = body["ids"].as_array() {
                for id in ids.iter().take(500) {
                    let id = int(id);
                    let mut tx = site.db.begin().await?;
                    sqlx::query("DELETE FROM comment_likes WHERE comment_id=?")
                        .bind(id)
                        .execute(&mut *tx)
                        .await?;
                    sqlx::query("DELETE FROM video_comments WHERE id=?")
                        .bind(id)
                        .execute(&mut *tx)
                        .await?;
                    tx.commit().await?;
                }
            }
            result = Some(json!({"success":true}));
        }
        ("GET", "/api/reports") => {
            result = Some(
                json!({"success":true,"reports":site.query("SELECT id,fanart_id,reporter_nickname,reason,fanart_title,fanart_image_url,fanart_author,status,created_at AS date FROM fanart_reports ORDER BY id DESC LIMIT 200",&[]).await?}),
            )
        }
        ("POST", "/api/admin/ip-ban") => {
            let ips = body["ips"]
                .as_array()
                .cloned()
                .unwrap_or_else(|| vec![body["ip"].clone()]);
            let mut banned = vec![];
            let own_ip = headers
                .get("cf-connecting-ip")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("127.0.0.1");
            for ip in ips.iter().take(500) {
                if let Some(ip) = ip
                    .as_str()
                    .and_then(|s| s.parse::<std::net::IpAddr>().ok())
                    .filter(|ip| !ip.is_loopback() && ip.to_string() != own_ip)
                {
                    site.execute("INSERT INTO banned_ips (ip,reason) VALUES (?,?) ON DUPLICATE KEY UPDATE reason=VALUES(reason)",&[json!(ip.to_string()),body["reason"].clone()]).await?;
                    banned.push(ip.to_string());
                }
            }
            site.refresh_controls().await?;
            result = Some(
                json!({"success":true,"confirmed":banned.iter().all(|ip|site.controls.read().unwrap().banned.contains(ip)),"banned":banned,"skipped":ips.iter().filter_map(Value::as_str).filter(|ip|!banned.iter().any(|b|b==ip)).collect::<Vec<_>>()}),
            );
        }
        ("POST", "/api/admin/ip-ban/non-kr") => {
            let own_ip = headers.get("cf-connecting-ip").and_then(|v| v.to_str().ok()).and_then(|s| s.parse::<std::net::IpAddr>().ok());
            let rows = site.query("SELECT DISTINCT ip_address AS ip FROM page_views WHERE ip_address IS NOT NULL AND NOT EXISTS (SELECT 1 FROM banned_ips b WHERE b.ip=page_views.ip_address)", &[]).await?;
            let mut banned = Vec::new();
            for row in rows {
                let Some(ip) = row["ip"].as_str().and_then(|s| s.parse::<std::net::IpAddr>().ok()) else { continue };
                if ip.is_loopback() || ip.is_unspecified() || ip.is_multicast() || Some(ip) == own_ip || site.country(&ip.to_string()) == "KR" { continue; }
                banned.push(ip.to_string());
            }
            banned.sort();
            banned.dedup();
            for batch in banned.chunks(500) {
                site.execute("INSERT IGNORE INTO banned_ips (ip,reason) SELECT jt.ip, ? FROM JSON_TABLE(?, '$[*]' COLUMNS(ip VARCHAR(45) PATH '$')) AS jt", &[json!("관리자 차단: 한국 외 국가 및 국가 미확인"), json!(json!(batch).to_string())]).await?;
            }
            site.refresh_controls().await?;
            let confirmed = banned.iter().all(|ip| site.controls.read().unwrap().banned.contains(ip));
            result = Some(json!({"success":true,"confirmed":confirmed,"banned":banned}));
        }
        ("POST", "/api/admin/ip-unban") => {
            let ip = body["ip"].as_str().unwrap_or("");
            let rows = site
                .query("SELECT reason FROM banned_ips WHERE ip=?", &[json!(ip)])
                .await?;
            let reason = rows
                .first()
                .and_then(|r| r["reason"].as_str())
                .unwrap_or("");
            if site.protected_bot(ip, reason) {
                return Ok(Some(error(
                    StatusCode::BAD_REQUEST,
                    "알려진 봇 IP는 차단 해제할 수 없습니다.",
                )));
            }
            site.execute("DELETE FROM banned_ips WHERE ip=?", &[body["ip"].clone()])
                .await?;
            site.refresh_controls().await?;
            result = Some(json!({"success":true}));
        }
        ("POST", "/api/admin/ip-unban/non-bots") => {
            let rows = site.query("SELECT ip,reason FROM banned_ips", &[]).await?;
            let mut released = 0;
            let mut protected = 0;
            for row in rows {
                let ip = row["ip"].as_str().unwrap_or("");
                if site.protected_bot(ip, row["reason"].as_str().unwrap_or("")) {
                    protected += 1;
                    continue;
                }
                site.execute("DELETE FROM banned_ips WHERE ip=?", &[json!(ip)])
                    .await?;
                released += 1;
            }
            site.refresh_controls().await?;
            result = Some(json!({"success":true,"released":released,"protected":protected}));
        }
        ("GET", "/api/admin/server-log") => {
            let file = site.root.join("logs/service.log");
            let errors_only = params.get("errors_only").map(String::as_str) == Some("1");
            let (lines, size) = tokio::task::spawn_blocking(move || {
                use std::io::{Read, Seek, SeekFrom};
                let mut lines: Vec<String> = vec![];
                let mut size = 0;
                if let Ok(mut f) = std::fs::File::open(file) {
                    size = f.metadata().map(|m| m.len()).unwrap_or(0);
                    let offset = size.saturating_sub(64 * 1024);
                    let _ = f.seek(SeekFrom::Start(offset));
                    let mut bytes = vec![];
                    let _ = f.take(64 * 1024).read_to_end(&mut bytes);
                    lines = String::from_utf8_lossy(&bytes)
                        .lines()
                        .skip(usize::from(offset > 0))
                        .filter(|line| !errors_only || line.to_lowercase().contains("error"))
                        .map(str::to_string)
                        .collect();
                    if lines.len() > 200 {
                        lines.drain(..lines.len() - 200);
                    }
                }
                (lines, size)
            })
            .await
            .unwrap_or_default();
            result = Some(json!({"success":true,"lines":lines,"size":size}));
        }
        ("POST", "/api/admin/logs/reset") => {
            let mut truncated = vec![];
            let mut removed = vec![];
            for name in [
                "service.log",
                "service.log.1",
                "service.log.2",
                "service.log.3",
            ] {
                let file = site.root.join("logs").join(name);
                if name.ends_with(".log") {
                    if std::fs::OpenOptions::new()
                        .write(true)
                        .truncate(true)
                        .open(file)
                        .is_ok()
                    {
                        truncated.push(name);
                    }
                } else if std::fs::remove_file(file).is_ok() {
                    removed.push(name);
                }
            }
            result = Some(json!({"success":true,"truncated":truncated,"removed":removed}));
        }
        ("GET", "/api/admin/stats") => {
            let totals = json!({"total_users":scalar(site,"SELECT COUNT(*) FROM users",&[]).await?,"new_users_14d":scalar(site,"SELECT COUNT(*) FROM users WHERE created_at>=NOW()-INTERVAL 14 DAY",&[]).await?,"total_fanart":scalar(site,"SELECT COUNT(*) FROM fanart_gallery",&[]).await?,"total_comments":scalar(site,"SELECT COUNT(*) FROM video_comments",&[]).await?});
            result = Some(
                json!({"success":true,"totals":totals,"top_keywords":site.query("SELECT keyword,search_count FROM search_trends ORDER BY search_count DESC LIMIT 10",&[]).await?,"fanart_daily":site.query("SELECT DATE_FORMAT(created_at,'%Y-%m-%d') AS date,COUNT(*) AS count FROM fanart_gallery WHERE created_at>=NOW()-INTERVAL 14 DAY GROUP BY date ORDER BY date",&[]).await?,"comments_daily":site.query("SELECT DATE_FORMAT(created_at,'%Y-%m-%d') AS date,COUNT(*) AS count FROM video_comments WHERE created_at>=NOW()-INTERVAL 14 DAY GROUP BY date ORDER BY date",&[]).await?,"reports_by_status":site.query("SELECT status,COUNT(*) AS c FROM fanart_reports GROUP BY status",&[]).await?}),
            );
        }
        ("POST", "/api/admin/traffic/reset-today") => {
            site.execute("DELETE FROM page_views WHERE viewed_at>=CURDATE() AND viewed_at<CURDATE()+INTERVAL 1 DAY", &[]).await?;
            result = Some(json!({"success":true}));
        }
        ("POST", "/api/admin/traffic/reset-ips") => {
            site.execute("DELETE FROM page_views", &[]).await?;
            result = Some(json!({"success":true}));
        }
        ("POST", "/api/admin/blocked-paths/reset") => {
            site.execute("DELETE FROM blocked_paths", &[]).await?;
            site.refresh_controls().await?;
            result = Some(json!({"success":true}));
        }
        ("GET", "/api/admin/traffic") => {
            result = Some(
                json!({"success":true,"daily":site.query("SELECT DATE_FORMAT(viewed_at,'%Y-%m-%d') AS date,COUNT(*) AS views,COUNT(DISTINCT ip_address) AS visitors FROM page_views WHERE viewed_at>=NOW()-INTERVAL 14 DAY GROUP BY date ORDER BY date",&[]).await?,"hourly":site.query("SELECT HOUR(viewed_at) AS hour,COUNT(*) AS views FROM page_views WHERE DATE(viewed_at)=CURDATE() GROUP BY hour ORDER BY hour",&[]).await?,"online_now":scalar(site,"SELECT COUNT(DISTINCT ip_address) FROM page_views WHERE viewed_at>=NOW()-INTERVAL 5 MINUTE",&[]).await?,"top_paths":site.query("SELECT path,COUNT(*) AS views FROM page_views WHERE DATE(viewed_at)=CURDATE() GROUP BY path ORDER BY views DESC LIMIT 1000",&[]).await?,"by_ip":[],"banned_ips":site.query("SELECT ip,reason,banned_at FROM banned_ips",&[]).await?,"blocked_paths":site.query("SELECT path,blocked_at FROM blocked_paths",&[]).await?,"rate_limit_enabled":true,"rate_limit_window_sec":1,"rate_limit_max_requests":8,"rate_limit_ban_minutes":0,"rate_limit_ips":[],"ip_countries":{}}),
            );
        }
        _ => {}
    }
    if parts.len() >= 4 && parts[0] == "api" && parts[1] == "admin" && parts[2] == "users" {
        let target = parts[3].parse::<i64>().unwrap_or(0);
        if target < 1 {
            return Ok(Some(error(
                StatusCode::BAD_REQUEST,
                "잘못된 회원 ID입니다.",
            )));
        }
        if method == "POST" && parts.len() == 5 {
            match parts[4] {
                "staff" => {
                    let role = body["staff_role"]
                        .as_str()
                        .unwrap_or("")
                        .chars()
                        .take(50)
                        .collect::<String>();
                    site.execute(
                        "UPDATE users SET is_staff=?,staff_role=? WHERE id=?",
                        &[json!(body["is_staff"] == true), json!(role), json!(target)],
                    )
                    .await?;
                    result = Some(json!({"success":true}));
                }
                "suspend" => {
                    if target == uid {
                        return Ok(Some(error(
                            StatusCode::BAD_REQUEST,
                            "현재 관리자 계정을 정지할 수 없습니다.",
                        )));
                    }
                    site.execute("UPDATE users SET suspended=1,suspended_reason=?,suspended_at=NOW(),session_epoch=COALESCE(session_epoch,0)+1 WHERE id=?",&[body["reason"].clone(),json!(target)]).await?;
                    result = Some(json!({"success":true}));
                }
                "unsuspend" => {
                    site.execute("UPDATE users SET suspended=0,suspended_reason=NULL,suspended_at=NULL WHERE id=?",&[json!(target)]).await?;
                    result = Some(json!({"success":true}));
                }
                _ => {}
            }
        }
        if method == "GET" && parts.len() == 5 && parts[4] == "ip-history" {
            result = Some(
                json!({"success":true,"history":site.query("SELECT id,ip_address AS ip,created_at AS date FROM user_login_log WHERE user_id=? ORDER BY id DESC LIMIT 200",&[json!(target)]).await?,"ip_countries":{}}),
            );
        }
        if method == "GET" && parts.len() == 5 && parts[4] == "summary" {
            let mut summary = json!({});
            for (key, table, col) in [
                ("fanart_count", "fanart_gallery", "user_id"),
                ("comment_count", "video_comments", "user_id"),
                ("post_count", "community_posts", "user_id"),
                ("reported_count", "fanart_reports", "reporter_user_id"),
                ("attendance_days", "user_attendance", "user_id"),
                ("login_count", "user_login_log", "user_id"),
            ] {
                summary[key] = scalar(
                    site,
                    &format!("SELECT COUNT(*) FROM {table} WHERE {col}=?"),
                    &[json!(target)],
                )
                .await?;
            }
            result = Some(json!({"success":true,"summary":summary}));
        }
    }
    if method == "GET" && path.starts_with("/api/admin/traffic/ip/") {
        let ip =
            percent_encoding::percent_decode_str(path.trim_start_matches("/api/admin/traffic/ip/"))
                .decode_utf8_lossy();
        result = Some(
            json!({"success":true,"ip":ip,"country":"","views":site.query("SELECT path,viewed_at FROM page_views WHERE ip_address=? ORDER BY id DESC LIMIT 300",&[json!(ip)]).await?,"banned":site.controls.read().unwrap().banned.contains(ip.as_ref())}),
        );
    }
    if let Some(data) = result {
        let mut data = data;
        if let Some(rows) = data["banned_ips"].as_array_mut() {
            for row in rows {
                row["known_bot"] = json!(site.protected_bot(
                    row["ip"].as_str().unwrap_or(""),
                    row["reason"].as_str().unwrap_or("")
                ));
            }
        }
        // Both loading and saving must return the same settings contract.
        if path == "/api/admin/site-settings" && method == "POST" {
            let controls = site.controls.read().unwrap();
            let mut settings = json!({});
            for (key, value) in &controls.settings {
                settings[key] = if key.ends_with("_enabled") {
                    json!(value == "1")
                } else if key == "banned_words" {
                    serde_json::from_str(value).unwrap_or(json!([]))
                } else {
                    json!(value)
                };
            }
            data["settings"] = settings;
        }
        let mut countries = json!({});
        for key in ["by_ip", "history", "banned_ips"] {
            if let Some(rows) = data[key].as_array() {
                for row in rows {
                    if let Some(ip) = row["ip"].as_str().or_else(|| row["ip_address"].as_str()) {
                        countries[ip] = json!(site.country(ip));
                    }
                }
            }
        }
        if !countries.as_object().unwrap().is_empty() {
            data["ip_countries"] = countries;
        }
        if path == "/api/admin/stats" {
            let today = (chrono::Utc::now() + chrono::Duration::hours(9)).date_naive();
            for key in ["fanart_daily", "comments_daily"] {
                let rows = data[key].as_array().cloned().unwrap_or_default();
                data[key] = json!(
                    (0..14)
                        .rev()
                        .map(|n| {
                            let date = (today - chrono::Duration::days(n)).to_string();
                            rows.iter()
                                .find(|r| r["date"] == date)
                                .cloned()
                                .unwrap_or(json!({"date":date,"count":0}))
                        })
                        .collect::<Vec<_>>()
                );
            }
        }
        if path == "/api/admin/traffic" {
            let rows = data["hourly"].as_array().cloned().unwrap_or_default();
            data["hourly"] = json!(
                (0..24)
                    .map(|h| rows
                        .iter()
                        .find(|r| r["hour"] == h)
                        .cloned()
                        .unwrap_or(json!({"hour":h,"views":0})))
                    .collect::<Vec<_>>()
            );
            let date = params
                .get("date")
                .filter(|d| chrono::NaiveDate::parse_from_str(d, "%Y-%m-%d").is_ok());
            if params.get("date").is_some_and(|d| !d.is_empty()) && date.is_none() {
                return Ok(Some(output(
                    json!({"success":false,"message":"올바른 날짜를 선택해 주세요."}),
                )));
            }
            let filter = if date.is_some() {
                " AND viewed_at >= ? AND viewed_at < DATE_ADD(?, INTERVAL 1 DAY)"
            } else {
                ""
            };
            let args = date.map(|d| vec![json!(d), json!(d)]).unwrap_or_default();
            let mut ips=site.query(&format!("SELECT ip_address AS ip,COUNT(*) AS views,COUNT(DISTINCT path) AS paths,MIN(viewed_at) AS first_seen,MAX(viewed_at) AS last_seen FROM page_views WHERE ip_address IS NOT NULL AND ip_address NOT IN ('','127.0.0.1','::1') AND NOT EXISTS (SELECT 1 FROM banned_ips b WHERE b.ip=page_views.ip_address){filter} GROUP BY ip ORDER BY views DESC LIMIT 1000"),&args).await?;
            let paths=site.query(&format!("SELECT ip_address AS ip,path,COUNT(*) AS views FROM page_views WHERE ip_address IS NOT NULL{filter} GROUP BY ip,path ORDER BY views DESC LIMIT 5000"),&args).await?;
            data["dates"] = json!(site.query("SELECT DISTINCT DATE_FORMAT(viewed_at,'%Y-%m-%d') AS date FROM page_views ORDER BY date DESC", &[]).await?);
            for row in &mut ips {
                if let Some(ip) = row["ip"].as_str() {
                    data["ip_countries"][ip] = json!(site.country(ip));
                }
                row["top_paths"] = json!(
                    paths
                        .iter()
                        .filter(|p| p["ip"] == row["ip"])
                        .take(5)
                        .map(|p| json!({"path":p["path"],"views":p["views"]}))
                        .collect::<Vec<_>>()
                );
            }
            data["by_ip"] = json!(ips);
            if let Some(rows) = data["by_ip"].as_array_mut() {
                let controls = site.controls.read().unwrap();
                rows.retain(|row| {
                    row["ip"]
                        .as_str()
                        .is_some_and(|ip| !controls.banned.contains(ip))
                });
            }
        }
        if method != "GET" {
            site.execute("INSERT INTO admin_audit_log (admin_email,admin_nickname,action,target_type,target_id,detail,ip_address) VALUES (?,?,?,?,?,?,?)",&[user["email"].clone(),user["nickname"].clone(),json!(path),json!("request"),json!(""),json!("Rust 관리자 처리"),json!(headers.get("cf-connecting-ip").and_then(|v|v.to_str().ok()).unwrap_or("127.0.0.1"))]).await?;
        }
        return Ok(Some(output(data)));
    }
    Ok(None)
}
