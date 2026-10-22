use super::*;
pub async fn api(
    site: &Site,
    path: &str,
    method: &str,
    params: &HashMap<String, String>,
    headers: &HeaderMap,
    session: &Value,
    body: &Value,
) -> Result<Option<Response>, sqlx::Error> {
    let id = session["user_id"].as_i64().unwrap_or(0);
    let recognized = path.starts_with("/api/bookmarks")
        || path.starts_with("/api/playlists")
        || path.starts_with("/api/history")
        || path.starts_with("/api/attendance");
    if !recognized {
        return Ok(None);
    }
    if id == 0 && !(method == "GET" && path.starts_with("/api/playlists/")) {
        return Ok(Some(match path {
            "/api/bookmarks/check" => output(json!({"bookmarked":false})),
            "/api/attendance/status" => output(json!({"logged_in":false})),
            _ => error(StatusCode::UNAUTHORIZED, "로그인이 필요합니다."),
        }));
    }
    let uid = json!(id);
    let ip = json!(
        headers
            .get("cf-connecting-ip")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("127.0.0.1")
    );
    let video_id = body["videoId"].as_str().unwrap_or("");
    let ok = || Some(output(json!({"success":true})));
    match (method, path) {
        ("GET", "/api/bookmarks/check") => {
            return Ok(Some(output(
                json!({"bookmarked":!site.query("SELECT id FROM user_bookmarks WHERE user_id=? AND video_id=?",&[uid,json!(params.get("videoId").cloned().unwrap_or_default())]).await?.is_empty()}),
            )));
        }
        ("GET", "/api/bookmarks") => {
            return Ok(Some(output(
                json!({"success":true,"bookmarks":site.query("SELECT video_id AS videoId,title,thumbnail,DATE_FORMAT(created_at,'%Y-%m-%d') AS date FROM user_bookmarks WHERE user_id=? ORDER BY created_at DESC LIMIT 500",&[uid]).await?}),
            )));
        }
        ("POST", "/api/bookmarks") => {
            if video_id.is_empty() {
                return Ok(Some(error(StatusCode::BAD_REQUEST, "videoId가 없습니다.")));
            }
            let mut tx = site.db.begin().await?;
            let found = sqlx::query(
                "SELECT id FROM user_bookmarks WHERE user_id=? AND video_id=? FOR UPDATE",
            )
            .bind(id)
            .bind(video_id)
            .fetch_optional(&mut *tx)
            .await?
            .is_some();
            if found {
                sqlx::query("DELETE FROM user_bookmarks WHERE user_id=? AND video_id=?")
                    .bind(id)
                    .bind(video_id)
                    .execute(&mut *tx)
                    .await?;
            } else {
                sqlx::query("INSERT INTO user_bookmarks (user_id,video_id,title,thumbnail,ip_address) VALUES (?,?,?,?,?)").bind(id).bind(video_id).bind(body["title"].as_str().unwrap_or("제목 없음")).bind(body["thumbnail"].as_str().unwrap_or("")).bind(ip.as_str()).execute(&mut *tx).await?;
            }
            tx.commit().await?;
            return Ok(Some(output(
                json!({"success":true,"bookmarked":!found,"message":if found{"북마크가 취소되었습니다."}else{"북마크에 추가되었습니다."}}),
            )));
        }
        ("GET", "/api/history") => {
            return Ok(Some(output(
                json!({"success":true,"videos":site.query("SELECT video_id AS videoId,title,thumbnail FROM watch_history WHERE user_id=? ORDER BY watched_at DESC LIMIT 100",&[uid]).await?}),
            )));
        }
        ("POST", "/api/history/add") => {
            if video_id.is_empty() {
                return Ok(Some(error(StatusCode::BAD_REQUEST, "videoId가 없습니다.")));
            }
            site.execute("INSERT INTO watch_history (user_id,video_id,title,thumbnail,ip_address,watched_at) VALUES (?,?,?,?,?,NOW()) ON DUPLICATE KEY UPDATE title=VALUES(title),thumbnail=VALUES(thumbnail),ip_address=VALUES(ip_address),watched_at=NOW()",&[uid,json!(video_id),body["title"].clone(),body["thumbnail"].clone(),ip]).await?;
            return Ok(ok());
        }
        ("POST", "/api/history/clear") => {
            site.execute("DELETE FROM watch_history WHERE user_id=?", &[uid])
                .await?;
            return Ok(ok());
        }
        ("POST", "/api/history/delete") => {
            if let Some(ids) = body["videoIds"].as_array() {
                for video in ids.iter().take(100) {
                    site.execute(
                        "DELETE FROM watch_history WHERE user_id=? AND video_id=?",
                        &[uid.clone(), video.clone()],
                    )
                    .await?;
                }
            }
            return Ok(ok());
        }
        ("GET", "/api/playlists") => {
            return Ok(Some(output(
                json!({"success":true,"playlists":site.query("SELECT p.playlist_id,p.title,p.privacy,p.created_at,(SELECT COUNT(*) FROM user_playlist_videos v WHERE v.playlist_id=p.playlist_id) AS video_count FROM user_playlists p WHERE user_id=? ORDER BY created_at DESC LIMIT 200",&[uid]).await?}),
            )));
        }
        ("POST", "/api/playlists") => {
            let title = body["title"].as_str().unwrap_or("").trim();
            if title.is_empty() || title.chars().count() > 255 {
                return Ok(Some(error(StatusCode::BAD_REQUEST, "제목을 확인해주세요.")));
            }
            let pid = format!("PL_{:016x}", rand::random::<u64>());
            let privacy = body["privacy"]
                .as_str()
                .filter(|v| ["public", "private"].contains(v))
                .unwrap_or("private");
            site.execute("INSERT INTO user_playlists (playlist_id,user_id,title,privacy,ip_address) VALUES (?,?,?,?,?)",&[json!(pid),uid,json!(title),json!(privacy),ip]).await?;
            return Ok(Some(output(
                json!({"success":true,"playlist_id":pid,"privacy":privacy}),
            )));
        }
        ("GET", "/api/attendance/status") => {
            let today = (chrono::Utc::now() + chrono::Duration::hours(9))
                .date_naive()
                .to_string();
            return Ok(Some(output(
                json!({"logged_in":true,"attended_today":!site.query("SELECT id FROM user_attendance WHERE user_id=? AND attendance_date=?",&[uid,json!(today)]).await?.is_empty()}),
            )));
        }
        ("POST", "/api/attendance/mark") => {
            let today = (chrono::Utc::now() + chrono::Duration::hours(9)).date_naive();
            let yesterday = today.pred_opt().unwrap();
            let mut tx = site.db.begin().await?;
            let user=sqlx::query("SELECT total_attendance,consecutive_attendance,last_attendance_date FROM users WHERE id=? FOR UPDATE").bind(id).fetch_one(&mut *tx).await?;
            let last = user
                .try_get::<Option<chrono::NaiveDate>, _>("last_attendance_date")
                .unwrap_or(None);
            if last == Some(today) {
                return Ok(Some(output(
                    json!({"success":false,"message":"이미 출석했습니다."}),
                )));
            }
            let total = user.try_get::<i32, _>("total_attendance").unwrap_or(0) + 1;
            let consecutive = if last == Some(yesterday) {
                user.try_get::<i32, _>("consecutive_attendance")
                    .unwrap_or(0)
                    + 1
            } else {
                1
            };
            sqlx::query(
                "INSERT INTO user_attendance (user_id,attendance_date,ip_address) VALUES (?,?,?)",
            )
            .bind(id)
            .bind(today)
            .bind(ip.as_str())
            .execute(&mut *tx)
            .await?;
            sqlx::query("UPDATE users SET total_attendance=?,consecutive_attendance=?,last_attendance_date=? WHERE id=?").bind(total).bind(consecutive).bind(today).bind(id).execute(&mut *tx).await?;
            tx.commit().await?;
            return Ok(Some(output(
                json!({"success":true,"total_attendance":total,"consecutive_attendance":consecutive,"message":"출석했습니다."}),
            )));
        }
        ("GET", "/api/attendance/calendar") => {
            use chrono::Datelike;
            let now = chrono::Utc::now() + chrono::Duration::hours(9);
            let year = params
                .get("year")
                .and_then(|v| v.parse::<i32>().ok())
                .unwrap_or(now.year());
            let month = params
                .get("month")
                .and_then(|v| v.parse::<u32>().ok())
                .unwrap_or(now.month());
            let Some(start) = chrono::NaiveDate::from_ymd_opt(year, month, 1) else {
                return Ok(Some(error(StatusCode::BAD_REQUEST, "날짜를 확인해주세요.")));
            };
            let end = start.checked_add_months(chrono::Months::new(1)).unwrap();
            let dates=site.query("SELECT DATE_FORMAT(attendance_date,'%Y-%m-%d') AS date FROM user_attendance WHERE user_id=? AND attendance_date>=? AND attendance_date<? ORDER BY attendance_date",&[uid,json!(start.to_string()),json!(end.to_string())]).await?.into_iter().map(|v|v["date"].clone()).collect::<Vec<_>>();
            return Ok(Some(output(
                json!({"success":true,"year":year,"month":month,"dates":dates}),
            )));
        }
        _ => {}
    }
    if let Some(rest) = path.strip_prefix("/api/playlists/") {
        let parts: Vec<_> = rest.split('/').collect();
        let pid = parts[0];
        let rows=site.query("SELECT playlist_id,user_id,title,privacy,created_at FROM user_playlists WHERE playlist_id=?",&[json!(pid)]).await?;
        let Some(playlist) = rows.first() else {
            return Ok(Some(error(
                StatusCode::NOT_FOUND,
                "재생목록을 찾을 수 없습니다.",
            )));
        };
        let owner = playlist["user_id"] == id;
        if !owner && (method != "GET" || playlist["privacy"] != "public") {
            return Ok(Some(error(
                StatusCode::FORBIDDEN,
                "접근할 수 없는 재생목록입니다.",
            )));
        }
        match (method, parts.len()) {
            ("GET", 1) => {
                return Ok(Some(output(
                    json!({"success":true,"playlist":playlist,"videos":site.query("SELECT video_id AS videoId,title,thumbnail,added_at FROM user_playlist_videos WHERE playlist_id=? ORDER BY id DESC LIMIT 1000",&[json!(pid)]).await?}),
                )));
            }
            ("DELETE", 1) => {
                let mut tx = site.db.begin().await?;
                sqlx::query("DELETE FROM user_playlist_videos WHERE playlist_id=?")
                    .bind(pid)
                    .execute(&mut *tx)
                    .await?;
                sqlx::query("DELETE FROM user_playlists WHERE playlist_id=? AND user_id=?")
                    .bind(pid)
                    .bind(id)
                    .execute(&mut *tx)
                    .await?;
                tx.commit().await?;
                return Ok(ok());
            }
            ("POST", 2) if parts[1] == "videos" => {
                if video_id.is_empty() {
                    return Ok(Some(error(StatusCode::BAD_REQUEST, "videoId가 없습니다.")));
                }
                site.execute("INSERT IGNORE INTO user_playlist_videos (playlist_id,video_id,title,thumbnail) VALUES (?,?,?,?)",&[json!(pid),json!(video_id),body["title"].clone(),body["thumbnail"].clone()]).await?;
                return Ok(Some(output(json!({"success":true,"added":true}))));
            }
            ("DELETE", 3) if parts[1] == "videos" => {
                site.execute(
                    "DELETE FROM user_playlist_videos WHERE playlist_id=? AND video_id=?",
                    &[json!(pid), json!(parts[2])],
                )
                .await?;
                return Ok(ok());
            }
            _ => {}
        }
    }
    Ok(None)
}
