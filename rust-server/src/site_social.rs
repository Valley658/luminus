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
    let uid = session["user_id"].as_i64().unwrap_or(0);
    let id = json!(uid);
    let parts: Vec<_> = path.trim_matches('/').split('/').collect();
    let ip = json!(
        headers
            .get("cf-connecting-ip")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("127.0.0.1")
    );
    if method == "GET" && matches!(path, "/api/fanart" | "/api/fanart/my") {
        if path.ends_with("/my") && uid == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        let filter = if path.ends_with("/my") {
            "f.user_id=?"
        } else {
            "f.status='active'"
        };
        let args = if path.ends_with("/my") {
            vec![id]
        } else {
            vec![]
        };
        let sql = format!(
            "SELECT f.id,f.user_id,f.nickname,f.image_url,f.title,f.description,f.status,f.thumbnail_url,f.lqip,DATE_FORMAT(f.created_at,'%Y-%m-%d') AS date,(SELECT COUNT(*) FROM fanart_reactions WHERE fanart_id=f.id AND reaction_type='like') AS like_count,(SELECT COUNT(*) FROM fanart_reactions WHERE fanart_id=f.id AND reaction_type='dislike') AS dislike_count,(SELECT COUNT(*) FROM fanart_comments WHERE fanart_id=f.id) AS comments_count FROM fanart_gallery f WHERE {filter} ORDER BY f.id DESC LIMIT 200"
        );
        return Ok(Some(output(
            json!({"success":true,"fanarts":site.query(&sql,&args).await?}),
        )));
    }
    if method == "GET" && parts.len() == 4 && parts[1] == "community" && parts[3] == "posts" {
        let member = percent_encoding::percent_decode_str(parts[2]).decode_utf8_lossy();
        let order = if params.get("sort").map(String::as_str) == Some("popular") {
            "likes_count DESC,p.created_at DESC"
        } else {
            "p.created_at DESC"
        };
        let sql = format!(
            "SELECT p.id,p.member_name,p.user_id,p.nickname,p.picture,p.title,p.content,p.video_url,p.image_url,p.created_at,(SELECT COUNT(*) FROM community_likes WHERE target_type='post' AND target_id=p.id AND reaction='like') AS likes_count,(SELECT COUNT(*) FROM community_comments WHERE post_id=p.id) AS comments_count,(SELECT reaction FROM community_likes WHERE target_type='post' AND target_id=p.id AND user_id=?) AS my_reaction FROM community_posts p WHERE p.member_name=? ORDER BY {order} LIMIT 200"
        );
        let mut posts = site.query(&sql, &[id, json!(member)]).await?;
        for p in &mut posts {
            p["images"] = p["image_url"]
                .as_str()
                .and_then(|s| serde_json::from_str::<Value>(s).ok())
                .filter(|v| v.is_array())
                .unwrap_or_else(|| {
                    p["image_url"]
                        .as_str()
                        .filter(|s| !s.is_empty())
                        .map(|s| json!([s]))
                        .unwrap_or(json!([]))
                });
        }
        return Ok(Some(output(json!({"success":true,"posts":posts}))));
    }
    // Comments use an allowlisted table and parent key, never user-controlled SQL identifiers.
    let comment = if parts.len() == 3 && parts[1] == "comments" {
        Some(("video_comments", "video_id", parts[2], "comment_likes"))
    } else if parts.len() == 4 && parts[1] == "community" && parts[2] == "comments" {
        Some(("community_comments", "post_id", parts[3], ""))
    } else if parts.len() == 4
        && parts[1] == "fanart"
        && parts[3] == "comments"
        && parts[2] != "comments"
    {
        Some(("fanart_comments", "fanart_id", parts[2], ""))
    } else {
        None
    };
    if let Some((table, parent, target, reactions)) = comment {
        let target = if parent == "video_id" {
            json!(target)
        } else {
            json!(target.parse::<i64>().unwrap_or(0))
        };
        if method == "GET" {
            let counts = if reactions.is_empty() {
                "0 AS like_count,0 AS dislike_count,NULL AS my_reaction".to_string()
            } else {
                format!(
                    "(SELECT COUNT(*) FROM {reactions} WHERE comment_id=c.id AND reaction='like') AS like_count,(SELECT COUNT(*) FROM {reactions} WHERE comment_id=c.id AND reaction='dislike') AS dislike_count,(SELECT reaction FROM {reactions} WHERE comment_id=c.id AND user_id={uid}) AS my_reaction"
                )
            };
            let image = if table == "video_comments" {
                ",c.image_url"
            } else {
                ""
            };
            let sql = format!(
                "SELECT c.id,c.user_id,c.nickname,c.picture,c.content,DATE_FORMAT(c.created_at,'%Y-%m-%d %H:%i') AS date,{counts}{image} FROM {table} c WHERE c.{parent}=? ORDER BY c.id DESC LIMIT 200"
            );
            let mut comments = site.query(&sql, &[target]).await?;
            for c in &mut comments {
                c["is_mine"] = json!(uid != 0 && c["user_id"] == uid);
            }
            return Ok(Some(output(json!({"success":true,"comments":comments}))));
        }
        if method == "POST" {
            if uid == 0 {
                return Ok(Some(error(
                    StatusCode::UNAUTHORIZED,
                    "로그인이 필요합니다.",
                )));
            }
            let content = body["content"].as_str().unwrap_or("").trim();
            if content.is_empty() || content.chars().count() > 500 {
                return Ok(Some(error(
                    StatusCode::BAD_REQUEST,
                    "내용은 1~500자로 입력해주세요.",
                )));
            }
            let user = site
                .query(
                    "SELECT nickname,picture FROM users WHERE id=?",
                    &[id.clone()],
                )
                .await?
                .remove(0);
            if parent != "video_id" {
                let parent_table = if table == "fanart_comments" {
                    "fanart_gallery"
                } else {
                    "community_posts"
                };
                if site
                    .query(
                        &format!("SELECT id FROM {parent_table} WHERE id=?"),
                        &[target.clone()],
                    )
                    .await?
                    .is_empty()
                {
                    return Ok(Some(error(
                        StatusCode::NOT_FOUND,
                        "게시물을 찾을 수 없습니다.",
                    )));
                }
            }
            let image = body["image_url"].as_str().filter(|s| {
                s.starts_with("/static/uploads/community/image_")
                    && !s.contains("..")
                    && !s.contains('\\')
            });
            let extra = if table == "video_comments" {
                ",image_url"
            } else {
                ""
            };
            let placeholder = if extra.is_empty() { "" } else { ",?" };
            let mut args = vec![
                target,
                id,
                user["nickname"].clone(),
                user["picture"].clone(),
                json!(content),
                ip,
            ];
            if !extra.is_empty() {
                args.push(json!(image));
            }
            let sql = format!(
                "INSERT INTO {table} ({parent},user_id,nickname,picture,content,ip_address{extra}) VALUES (?,?,?,?,?,?{placeholder})"
            );
            let cid = site.execute(&sql, &args).await?;
            return Ok(Some(output(json!({"success":true,"id":cid}))));
        }
    }
    if method == "POST"
        && parts.len() == 5
        && parts[2] == "comments"
        && parts[4] == "delete"
        && ["community", "fanart"].contains(&parts[1])
    {
        if uid == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        let table = if parts[1] == "community" {
            "community_comments"
        } else {
            "fanart_comments"
        };
        let cid = parts[3].parse::<i64>().unwrap_or(0);
        let user = site
            .query("SELECT nickname,email FROM users WHERE id=?", &[id.clone()])
            .await?
            .remove(0);
        let where_owner = if super::auth::admin(&user) {
            ""
        } else {
            " AND user_id=?"
        };
        let mut args = vec![json!(cid)];
        if !where_owner.is_empty() {
            args.push(id);
        }
        site.execute(
            &format!("DELETE FROM {table} WHERE id=?{where_owner}"),
            &args,
        )
        .await?;
        return Ok(Some(output(json!({"success":true}))));
    }
    if path == "/api/community/post" && method == "POST" {
        if uid == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        let content = body["content"].as_str().unwrap_or("").trim();
        let member = body["member_name"].as_str().unwrap_or("");
        if content.is_empty()
            || content.chars().count() > 5000
            || site
                .query(
                    "SELECT id FROM members WHERE member_name=?",
                    &[json!(member)],
                )
                .await?
                .is_empty()
        {
            return Ok(Some(error(
                StatusCode::BAD_REQUEST,
                "게시글 내용을 확인해주세요.",
            )));
        }
        let user = site
            .query(
                "SELECT nickname,picture FROM users WHERE id=?",
                &[id.clone()],
            )
            .await?
            .remove(0);
        let pid=site.execute("INSERT INTO community_posts (member_name,user_id,nickname,picture,title,content,image_url,video_url,ip_address) VALUES (?,?,?,?,?,?,?,?,?)",&[json!(member),id,user["nickname"].clone(),user["picture"].clone(),body["title"].clone(),json!(content),json!(body["images"].to_string()),body["video_url"].clone(),ip]).await?;
        return Ok(Some(output(json!({"success":true,"id":pid}))));
    }
    let react_video_comment =
        method == "POST" && parts.len() == 4 && parts[1] == "comments" && parts[3] == "react";
    let react_fanart =
        method == "POST" && parts.len() == 4 && parts[1] == "fanart" && parts[3] == "react";
    if react_fanart {
        if uid == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        let fid = parts[2].parse::<i64>().unwrap_or(0);
        let reaction = body["reaction"].as_str().unwrap_or("");
        let meta = site
            .query(
                "SELECT user_id,nickname,title,image_url FROM fanart_gallery WHERE id=?",
                &[json!(fid)],
            )
            .await?;
        let Some(meta) = meta.first() else {
            return Ok(Some(error(
                StatusCode::NOT_FOUND,
                "게시물을 찾을 수 없습니다.",
            )));
        };
        if reaction == "delete" {
            let user = site
                .query("SELECT nickname,email FROM users WHERE id=?", &[json!(uid)])
                .await?;
            if !user.first().is_some_and(super::auth::admin) {
                return Ok(Some(error(StatusCode::FORBIDDEN, "권한이 없습니다.")));
            }
            delete_fanart(site, fid).await?;
            return Ok(Some(output(json!({"success":true}))));
        }
        if !["like", "dislike", "report"].contains(&reaction) {
            return Ok(Some(error(StatusCode::BAD_REQUEST, "잘못된 반응입니다.")));
        }
        let mut tx = site.db.begin().await?;
        sqlx::query("SELECT id FROM fanart_gallery WHERE id=? FOR UPDATE")
            .bind(fid)
            .fetch_one(&mut *tx)
            .await?;
        let inserted=sqlx::query("INSERT IGNORE INTO fanart_reactions (fanart_id,user_id,reaction_type,ip_address) VALUES (?,?,?,?)").bind(fid).bind(uid).bind(reaction).bind(ip.as_str()).execute(&mut *tx).await?.rows_affected();
        if inserted == 0 {
            return Ok(Some(output(
                json!({"success":false,"message":"이미 반응을 남기셨습니다."}),
            )));
        }
        let mut auto_delete = false;
        if reaction == "report" {
            let reason = body["reason"]
                .as_str()
                .unwrap_or("사유 미입력")
                .chars()
                .take(100)
                .collect::<String>();
            sqlx::query("INSERT INTO fanart_reports (fanart_id,reporter_user_id,reporter_nickname,reason,fanart_title,fanart_image_url,fanart_author,ip_address) VALUES (?,?,?,?,?,?,?,?)").bind(fid).bind(uid).bind(session["user_nickname"].as_str()).bind(&reason).bind(meta["title"].as_str()).bind(meta["image_url"].as_str()).bind(meta["nickname"].as_str()).bind(ip.as_str()).execute(&mut *tx).await?;
            let count:i64=sqlx::query_scalar("SELECT COUNT(*) FROM fanart_reactions WHERE fanart_id=? AND reaction_type='report'").bind(fid).fetch_one(&mut *tx).await?;
            if count >= 3 {
                auto_delete = true;
                sqlx::query("UPDATE fanart_reports SET status='auto_deleted' WHERE fanart_id=?")
                    .bind(fid)
                    .execute(&mut *tx)
                    .await?;
                sqlx::query("INSERT INTO user_report_flags (user_id,nickname,strike_count,last_reason) VALUES (?,?,1,?) ON DUPLICATE KEY UPDATE strike_count=strike_count+1,last_reason=VALUES(last_reason)").bind(meta["user_id"].as_i64()).bind(meta["nickname"].as_str()).bind(reason).execute(&mut *tx).await?;
            }
        }
        tx.commit().await?;
        if auto_delete {
            delete_fanart(site, fid).await?;
        }
        return Ok(Some(output(json!({"success":true}))));
    }
    let community = path == "/api/community/like";
    let video_status =
        method == "GET" && parts.len() == 4 && parts[1] == "video" && parts[3] == "reaction";
    if react_video_comment || react_fanart || community || video_status {
        if method != "GET" && uid == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        let target = if community {
            body["target_id"].clone()
        } else {
            json!(parts[2])
        };
        let (table, target_column, reaction_column) = if react_video_comment {
            ("comment_likes", "comment_id", "reaction")
        } else if react_fanart {
            ("fanart_reactions", "fanart_id", "reaction_type")
        } else {
            ("community_likes", "target_id", "reaction")
        };
        let target_type = if video_status {
            "video"
        } else {
            body["target_type"].as_str().unwrap_or("post")
        };
        if !["post", "comment", "video"].contains(&target_type) {
            return Ok(Some(error(
                StatusCode::BAD_REQUEST,
                "잘못된 반응 대상입니다.",
            )));
        }
        let extra = if table == "community_likes" {
            format!(" AND target_type='{target_type}'")
        } else {
            String::new()
        };
        if method == "POST" {
            let reaction = body["reaction"]
                .as_str()
                .or_else(|| body["type"].as_str())
                .unwrap_or("");
            if !["like", "dislike", "report"].contains(&reaction)
                || (reaction == "report" && !react_fanart)
            {
                return Ok(Some(error(StatusCode::BAD_REQUEST, "잘못된 반응입니다.")));
            }
            let existing=site.query(&format!("SELECT id,{reaction_column} AS reaction FROM {table} WHERE {target_column}=? AND user_id=?{extra}"),&[target.clone(),id.clone()]).await?;
            site.execute(
                &format!("DELETE FROM {table} WHERE {target_column}=? AND user_id=?{extra}"),
                &[target.clone(), id.clone()],
            )
            .await?;
            if !existing.iter().any(|r| r["reaction"] == reaction) {
                let columns = if table == "community_likes" {
                    ",target_type"
                } else {
                    ""
                };
                let placeholder = if columns.is_empty() { "" } else { ",?" };
                let mut args = vec![target.clone(), id.clone(), json!(reaction), ip];
                if !columns.is_empty() {
                    args.push(json!(target_type));
                }
                site.execute(&format!("INSERT INTO {table} ({target_column},user_id,{reaction_column},ip_address{columns}) VALUES (?,?,?,?{placeholder})"),&args).await?;
            }
        }
        let rows=site.query(&format!("SELECT COALESCE(SUM({reaction_column}='like'),0) AS like_count,COALESCE(SUM({reaction_column}='dislike'),0) AS dislike_count,MAX(IF(user_id=?,{reaction_column},NULL)) AS my_reaction FROM {table} WHERE {target_column}=?{extra}"),&[id,target]).await?;
        let mut out = rows.into_iter().next().unwrap_or(json!({}));
        out["success"] = json!(true);
        return Ok(Some(output(out)));
    }
    Ok(None)
}
pub async fn delete_fanart(site: &Site, id: i64) -> Result<(), sqlx::Error> {
    let images = site
        .query(
            "SELECT image_url,thumbnail_url FROM fanart_gallery WHERE id=?",
            &[json!(id)],
        )
        .await?;
    let mut tx = site.db.begin().await?;
    for table in ["fanart_gallery", "fanart_comments", "fanart_reactions"] {
        let col = if table == "fanart_gallery" {
            "id"
        } else {
            "fanart_id"
        };
        sqlx::query(&format!("DELETE FROM {table} WHERE {col}=?"))
            .bind(id)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    let directory = site.root.join("static/images").canonicalize().ok();
    if let Some(directory) = directory {
        for row in images {
            for key in ["image_url", "thumbnail_url"] {
                if let Some(url) = row[key]
                    .as_str()
                    .and_then(|s| s.strip_prefix("/static/images/"))
                {
                    if let Ok(path) = directory.join(url).canonicalize() {
                        if path.parent() == Some(directory.as_path()) {
                            let _ = std::fs::remove_file(path);
                        }
                    }
                }
            }
        }
    }
    Ok(())
}
