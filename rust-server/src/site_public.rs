use super::*;
pub async fn api(
    site: &Site,
    path: &str,
    method: &str,
    params: &HashMap<String, String>,
    _headers: &HeaderMap,
    session: &Value,
    body: &Value,
) -> Result<Option<Response>, sqlx::Error> {
    let videos = site.videos.load_full();
    if method != "GET" {
        return Ok(None);
    }
    let uid = session["user_id"].as_i64().unwrap_or(0);
    match path {
        "/api/staff-roles" => {
            let mut roles = json!({});
            for r in site.query("SELECT nickname,COALESCE(NULLIF(staff_role,''),'운영진') AS role FROM users WHERE is_staff=1",&[]).await? {if let Some(n)=r["nickname"].as_str(){roles[n]=r["role"].clone();}}
            for pair in env("STAFF_ROLE_LABELS", "").split(',') {
                if let Some((nick, role)) = pair.split_once(':') {
                    roles[nick.trim()] = json!(role.trim());
                }
            }
            return Ok(Some(output(roles)));
        }
        "/api/site/notice" => {
            let mut settings = HashMap::new();
            for r in site
                .query("SELECT setting_key,setting_value FROM site_settings", &[])
                .await?
            {
                settings.insert(
                    r["setting_key"].as_str().unwrap_or("").to_string(),
                    r["setting_value"].as_str().unwrap_or("").to_string(),
                );
            }
            let val = |key: &str| settings.get(key).cloned().unwrap_or_default();
            let exempt = if uid != 0 {
                site.query(
                    "SELECT email,nickname,staff_role FROM users WHERE id=?",
                    &[json!(uid)],
                )
                .await?
                .first()
                .is_some_and(|u| {
                    super::auth::admin(u)
                        || u["staff_role"] == "개발자"
                        || u["email"] == env("DEV_EMAIL", "")
                })
            } else {
                false
            };
            return Ok(Some(output(
                json!({"success":true,"enabled":val("notice_enabled")=="1","text":val("notice_text"),"level":val("notice_level"),"link":val("notice_link"),"revision":val("notice_revision"),"maintenance":!exempt&&val("maintenance_enabled")=="1"}),
            )));
        }
        "/api/search/local_index" | "/api/search/suggest" => {
            let query = params.get("q").cloned().unwrap_or_default().to_lowercase();
            let mut items = vec![];
            for v in &*videos {
                if path.ends_with("local_index")
                    || v["title"]
                        .as_str()
                        .unwrap_or("")
                        .to_lowercase()
                        .contains(&query)
                {
                    items.push(json!({"type":"video","text":v["title"],"videoId":v["videoId"],"thumbnail":v["thumbnail"]}));
                    if items.len()
                        >= if path.ends_with("local_index") {
                            300
                        } else {
                            10
                        }
                    {
                        break;
                    }
                }
            }
            for m in site.query("SELECT member_name FROM members", &[]).await? {
                if path.ends_with("local_index")
                    || m["member_name"].as_str().unwrap_or("").contains(&query)
                {
                    items.push(json!({"type":"member","text":m["member_name"]}));
                }
            }
            return Ok(Some(output(if path.ends_with("local_index") {
                json!({"success":true,"items":items})
            } else {
                json!({"success":true,"suggestions":items})
            })));
        }
        "/api/search" => {
            let q = params.get("q").cloned().unwrap_or_default();
            if q.trim().is_empty() || q.chars().count() > 100 {
                return Ok(Some(error(
                    StatusCode::BAD_REQUEST,
                    "검색어를 확인해주세요.",
                )));
            }
            let lowercase = q.to_lowercase();
            let mut results: Vec<_> = videos
                .iter()
                .filter(|v| {
                    v["title"]
                        .as_str()
                        .unwrap_or("")
                        .to_lowercase()
                        .contains(&lowercase)
                        || v["member_name"].as_str().unwrap_or("").contains(&q)
                })
                .take(100)
                .cloned()
                .collect();
            let like = json!(format!(
                "%{}%",
                q.replace('\\', "\\\\")
                    .replace('%', "\\%")
                    .replace('_', "\\_")
            ));
            let mut archive=site.query("SELECT video_id AS videoId,video_id AS id,title,thumbnail,member_name,is_short FROM member_video_archive WHERE title LIKE ? OR member_name LIKE ? ORDER BY id DESC LIMIT 2000",&[like.clone(),like.clone()]).await?;
            for v in &mut archive {
                v["is_short"] = json!(v["is_short"] == 1);
            }
            let mut seen = results
                .iter()
                .filter_map(|v| v["videoId"].as_str().map(str::to_owned))
                .collect::<std::collections::HashSet<_>>();
            for v in archive {
                if seen.insert(v["videoId"].as_str().unwrap_or("").to_owned()) {
                    results.push(v);
                }
            }
            let community=site.query("SELECT id,member_name,nickname,title,content,picture,created_at FROM community_posts WHERE title LIKE ? OR content LIKE ? ORDER BY id DESC LIMIT 50",&[like.clone(),like.clone()]).await?;
            let fanarts=site.query("SELECT id,title,nickname,image_url,thumbnail_url,lqip FROM fanart_gallery WHERE status='active' AND (title LIKE ? OR description LIKE ?) ORDER BY id DESC LIMIT 50",&[like.clone(),like.clone()]).await?;
            let channels=site.query("SELECT channel_id,nickname,picture,channel_description AS description,(SELECT COUNT(*) FROM channel_videos WHERE channel_user_id=u.id) AS video_count FROM users u WHERE channel_id IS NOT NULL AND nickname LIKE ? LIMIT 20",&[like]).await?;
            return Ok(Some(output(
                json!({"success":true,"videos":results,"community":community,"fanarts":fanarts,"channels":channels}),
            )));
        }
        "/api/search/trending" => {
            return Ok(Some(output(
                json!({"success":true,"trends":site.query("SELECT keyword FROM search_trends WHERE search_count>=10 ORDER BY search_count DESC,last_searched DESC LIMIT 5",&[]).await?.into_iter().map(|v|v["keyword"].clone()).collect::<Vec<_>>()}),
            )));
        }
        "/api/schedules" => {
            use chrono::Datelike;
            let today = (chrono::Utc::now() + chrono::Duration::hours(9)).date_naive();
            let fallback =
                today - chrono::Duration::days(today.weekday().num_days_from_sunday() as i64);
            let start = params
                .get("start")
                .and_then(|s| chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
                .unwrap_or(fallback);
            let end = start + chrono::Duration::days(6);
            let mut schedules=site.query("SELECT id,member_name,DATE_FORMAT(event_date,'%Y-%m-%d') AS event_date,TIME_FORMAT(event_time,'%H:%i') AS event_time,title,is_day_off FROM member_schedules WHERE event_date>=? AND event_date<=? ORDER BY event_date,event_time,id",&[json!(start.to_string()),json!(end.to_string())]).await?;
            for s in &mut schedules {
                s["is_day_off"] = json!(s["is_day_off"] == 1);
            }
            return Ok(Some(output(
                json!({"success":true,"start":start.to_string(),"end":end.to_string(),"schedules":schedules}),
            )));
        }
        "/api/comment_previews" => {
            let mut map = json!({});
            for id in params
                .get("ids")
                .map(String::as_str)
                .unwrap_or("")
                .split(',')
                .take(30)
            {
                let rows=site.query("SELECT nickname,content FROM video_comments WHERE video_id=? ORDER BY id DESC LIMIT 1",&[json!(id)]).await?;
                if let Some(row) = rows.first() {
                    map[id] = row.clone();
                }
            }
            return Ok(Some(output(json!({"success":true,"comments":map}))));
        }
        _ => {}
    }
    if let Some(id) = path.strip_prefix("/api/comment_preview/") {
        let comments=site.query("SELECT nickname,content FROM video_comments WHERE video_id=? ORDER BY id DESC LIMIT 1",&[json!(id)]).await?;
        return Ok(Some(output(
            json!({"success":true,"comment":comments.first()}),
        )));
    }
    let _ = body;
    Ok(None)
}
