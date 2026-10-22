use super::*;
pub async fn api(
    site: &Site,
    path: &str,
    method: &str,
    _params: &HashMap<String, String>,
    headers: &HeaderMap,
    session: &Value,
    body: &Value,
) -> Result<Option<Response>, sqlx::Error> {
    let uid = session["user_id"].as_i64().unwrap_or(0);
    let id = json!(uid);
    let videos = site.videos.load_full();
    if method == "POST" && path == "/api/fanart/upload" {
        if uid == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        let image = body["image_url"]
            .as_str()
            .filter(|s| s.starts_with("/static/images/image_"));
        let Some(image) = image else {
            return Ok(Some(error(
                StatusCode::BAD_REQUEST,
                "이미지 파일이 없습니다.",
            )));
        };
        let title = body["title"]
            .as_str()
            .unwrap_or("")
            .chars()
            .take(50)
            .collect::<String>();
        let description = body["description"].as_str().unwrap_or("");
        let user = site
            .query("SELECT nickname FROM users WHERE id=?", &[id.clone()])
            .await?
            .remove(0);
        let fanart=site.execute("INSERT INTO fanart_gallery (user_id,nickname,image_url,title,description,thumbnail_url,lqip,ip_address) VALUES (?,?,?,?,?,?,?,?)",&[id,user["nickname"].clone(),json!(image),json!(title),json!(description),body["thumbnail_url"].clone(),json!(""),json!(headers.get("cf-connecting-ip").and_then(|v|v.to_str().ok()).unwrap_or("127.0.0.1"))]).await?;
        return Ok(Some(output(
            json!({"success":true,"id":fanart,"image_url":image}),
        )));
    }
    if method == "POST" && path == "/api/fanart/update" {
        if uid == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        site.execute(
            "UPDATE fanart_gallery SET title=?,description=? WHERE id=? AND user_id=?",
            &[
                body["title"].clone(),
                body["description"].clone(),
                body["id"].clone(),
                id,
            ],
        )
        .await?;
        return Ok(Some(output(json!({"success":true}))));
    }
    if !path.starts_with("/api/me/")
        && !path.starts_with("/api/profile/")
        && !path.starts_with("/api/channel/")
    {
        return Ok(None);
    }
    if uid == 0 && !path.starts_with("/api/channel/") {
        return Ok(Some(error(
            StatusCode::UNAUTHORIZED,
            "로그인이 필요합니다.",
        )));
    }
    if method == "GET" && path == "/api/profile/liked_videos" {
        let rows=site.query("SELECT target_id FROM community_likes WHERE target_type='video' AND reaction='like' AND user_id=? ORDER BY created_at DESC LIMIT 200",&[id]).await?;
        let mut liked = vec![];
        for r in rows {
            let vid = r["target_id"].as_str().unwrap_or("");
            let v=videos.iter().find(|v|v["videoId"]==vid).cloned().unwrap_or(json!({"videoId":vid,"title":"","thumbnail":format!("https://i.ytimg.com/vi_webp/{vid}/hqdefault.webp")}));
            liked.push(v);
        }
        return Ok(Some(output(json!({"success":true,"videos":liked}))));
    }
    if method == "GET" && path == "/api/profile/my_posts" {
        return Ok(Some(output(
            json!({"success":true,"posts":site.query("SELECT id,member_name,title,content,image_url,video_url,DATE_FORMAT(created_at,'%Y-%m-%d') AS date FROM community_posts WHERE user_id=? ORDER BY id DESC LIMIT 200",&[id]).await?}),
        )));
    }
    if method == "POST" && matches!(path, "/api/me/profile" | "/api/me/nickname") {
        let nickname = body["nickname"].as_str().unwrap_or("").trim();
        let user = site
            .query(
                "SELECT nickname,email,is_staff FROM users WHERE id=?",
                &[id.clone()],
            )
            .await?
            .remove(0);
        if nickname.is_empty()
            || nickname.chars().count() > 20
            || nickname
                .chars()
                .any(|c| c.is_control() || "<>`".contains(c))
            || (nickname != user["nickname"].as_str().unwrap_or("")
                && (user["is_staff"] == 1
                    || ["리도", "뱀늘이", "디자인담당", "개발자"]
                        .iter()
                        .any(|n| nickname.contains(n))))
        {
            return Ok(Some(error(
                StatusCode::BAD_REQUEST,
                "닉네임을 확인해주세요.",
            )));
        }
        site.execute(
            "UPDATE users SET nickname=? WHERE id=?",
            &[json!(nickname), id.clone()],
        )
        .await?;
        if let Some(picture) = body["picture"]
            .as_str()
            .filter(|s| s.starts_with("/static/uploads/profiles/image_"))
        {
            site.execute(
                "UPDATE users SET picture=? WHERE id=?",
                &[json!(picture), id.clone()],
            )
            .await?;
        }
        let user = site
            .query(
                "SELECT id,nickname,picture,session_epoch FROM users WHERE id=?",
                &[id],
            )
            .await?
            .remove(0);
        let mut r =
            output(json!({"success":true,"nickname":user["nickname"],"picture":user["picture"]}));
        let session = json!({"user_id":user["id"],"user_nickname":user["nickname"],"user_picture":user["picture"],"sv":user["session_epoch"]});
        r.headers_mut()
            .insert("set-cookie", site.cookie(&session).parse().unwrap());
        return Ok(Some(r));
    }
    if method == "GET" && path.starts_with("/api/channel/") {
        let cid = path.trim_start_matches("/api/channel/");
        let users=site.query("SELECT id,nickname,picture,created_at AS joined_at,channel_description AS description,channel_link AS link,channel_country AS country FROM users WHERE channel_id=?",&[json!(cid)]).await?;
        let Some(owner) = users.first() else {
            return Ok(Some(error(
                StatusCode::NOT_FOUND,
                "존재하지 않는 채널입니다.",
            )));
        };
        let oid = owner["id"].clone();
        let subscribers = site
            .query(
                "SELECT COUNT(*) AS count FROM channel_subscriptions WHERE channel_user_id=?",
                &[oid.clone()],
            )
            .await?;
        let videos=site.query("SELECT id,title,description,video_url,thumbnail,view_count,created_at FROM channel_videos WHERE channel_user_id=? AND visibility='public' ORDER BY id DESC LIMIT 100",&[oid.clone()]).await?;
        let posts=site.query("SELECT id,member_name,title,content,image_url,video_url,created_at FROM community_posts WHERE user_id=? ORDER BY id DESC LIMIT 50",&[oid.clone()]).await?;
        let subscribed=!site.query("SELECT id FROM channel_subscriptions WHERE subscriber_user_id=? AND channel_user_id=?",&[id,oid.clone()]).await?.is_empty();
        let mut channel = owner.clone();
        channel["subscriber_count"] = subscribers[0]["count"].clone();
        channel["video_count"] = json!(videos.len());
        channel["view_count"] = json!(
            videos
                .iter()
                .map(|v| v["view_count"].as_i64().unwrap_or(0))
                .sum::<i64>()
        );
        return Ok(Some(output(
            json!({"success":true,"channel_id":cid,"is_owner":oid==uid,"is_subscribed":subscribed,"channel":channel,"videos":videos,"posts":posts}),
        )));
    }
    if method == "POST" && path == "/api/channel/subscribe" {
        if uid == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        let users = site
            .query(
                "SELECT id FROM users WHERE channel_id=?",
                &[body["channel_id"].clone()],
            )
            .await?;
        let Some(owner) = users.first() else {
            return Ok(Some(error(
                StatusCode::NOT_FOUND,
                "존재하지 않는 채널입니다.",
            )));
        };
        let oid = owner["id"].clone();
        if oid == uid {
            return Ok(Some(error(
                StatusCode::BAD_REQUEST,
                "본인 채널은 구독할 수 없습니다.",
            )));
        }
        let found=!site.query("SELECT id FROM channel_subscriptions WHERE subscriber_user_id=? AND channel_user_id=?",&[id.clone(),oid.clone()]).await?.is_empty();
        if found {
            site.execute("DELETE FROM channel_subscriptions WHERE subscriber_user_id=? AND channel_user_id=?",&[id,oid]).await?;
        } else {
            site.execute("INSERT INTO channel_subscriptions (subscriber_user_id,channel_user_id) VALUES (?,?)",&[id,oid]).await?;
        }
        return Ok(Some(output(json!({"success":true,"subscribed":!found}))));
    }
    Ok(None)
}
