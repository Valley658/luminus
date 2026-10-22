use super::*;
pub static UPSTREAM: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(2);
pub async fn api(
    site: &Site,
    path: &str,
    method: &str,
    params: &HashMap<String, String>,
    headers: &HeaderMap,
    session: &Value,
    _body: &Value,
) -> Result<Option<Response>, sqlx::Error> {
    let videos = site.videos.load_full();
    if method != "GET" {
        return Ok(None);
    }
    if path == "/login/discord" {
        let client = env("DISCORD_OAUTH_CLIENT_ID", "");
        let redirect = env("DISCORD_OAUTH_REDIRECT_URI", "");
        if client.is_empty() || redirect.is_empty() {
            return Ok(Some(error(
                StatusCode::SERVICE_UNAVAILABLE,
                "디스코드 로그인이 설정되지 않았습니다.",
            )));
        }
        let mut session = session.clone();
        let state = format!("{:032x}", rand::random::<u128>());
        session["discord_oauth_state"] = json!(state);
        session["discord_oauth_intent"] = json!(if session["user_id"].as_i64().unwrap_or(0) != 0 {
            "link"
        } else {
            "login"
        });
        session["oauth_at"] = json!(chrono::Utc::now().timestamp());
        let mut url = reqwest::Url::parse("https://discord.com/api/oauth2/authorize").unwrap();
        url.query_pairs_mut().extend_pairs([
            ("client_id", client.as_str()),
            ("redirect_uri", redirect.as_str()),
            ("response_type", "code"),
            ("scope", "identify"),
            ("state", state.as_str()),
            ("prompt", "consent"),
        ]);
        let mut response = axum::response::Redirect::to(url.as_str()).into_response();
        response
            .headers_mut()
            .insert("set-cookie", site.cookie(&session).parse().unwrap());
        return Ok(Some(response));
    }
    if path == "/login/discord/callback" {
        use subtle::ConstantTimeEq;
        let expected = session["discord_oauth_state"].as_str().unwrap_or("");
        let returned = params.get("state").map(String::as_str).unwrap_or("");
        let code = params.get("code").map(String::as_str).unwrap_or("");
        let fail = || Some(axum::response::Redirect::to("/?login_error=1").into_response());
        if expected.is_empty()
            || !bool::from(expected.as_bytes().ct_eq(returned.as_bytes()))
            || code.is_empty()
            || chrono::Utc::now().timestamp() - session["oauth_at"].as_i64().unwrap_or(0) > 600
        {
            return Ok(fail());
        }
        let _permit = match UPSTREAM.try_acquire() {
            Ok(p) => p,
            Err(_) => {
                return Ok(Some(error(
                    StatusCode::TOO_MANY_REQUESTS,
                    "잠시 후 다시 시도해주세요.",
                )));
            }
        };
        let token = site
            .client
            .post("https://discord.com/api/oauth2/token")
            .form(&[
                ("client_id", env("DISCORD_OAUTH_CLIENT_ID", "")),
                ("client_secret", env("DISCORD_OAUTH_CLIENT_SECRET", "")),
                ("grant_type", "authorization_code".into()),
                ("code", code.into()),
                ("redirect_uri", env("DISCORD_OAUTH_REDIRECT_URI", "")),
            ])
            .send()
            .await;
        let token = match token {
            Ok(t) if t.status().is_success() => t.json::<Value>().await.ok(),
            _ => None,
        };
        let Some(access) = token.as_ref().and_then(|t| t["access_token"].as_str()) else {
            return Ok(fail());
        };
        let user = match site
            .client
            .get("https://discord.com/api/users/@me")
            .bearer_auth(access)
            .send()
            .await
        {
            Ok(u) if u.status().is_success() => u.json::<Value>().await.ok(),
            _ => None,
        };
        let Some(discord) = user else {
            return Ok(fail());
        };
        let Some(discord_id) = discord["id"].as_str() else {
            return Ok(fail());
        };
        let mut new_session = session.clone();
        new_session
            .as_object_mut()
            .unwrap()
            .remove("discord_oauth_state");
        new_session.as_object_mut().unwrap().remove("oauth_at");
        let uid = session["user_id"].as_i64().unwrap_or(0);
        if session["discord_oauth_intent"] == "link" && uid != 0 {
            if !site
                .query(
                    "SELECT id FROM users WHERE discord_id=? AND id<>?",
                    &[json!(discord_id), json!(uid)],
                )
                .await?
                .is_empty()
            {
                new_session["flash_discord_error"] = json!("이미 다른 계정에 연동되어 있습니다.");
            } else {
                site.execute(
                    "UPDATE users SET discord_id=?,discord_username=? WHERE id=?",
                    &[json!(discord_id), discord["username"].clone(), json!(uid)],
                )
                .await?;
                new_session["flash_discord_linked"] = json!(true);
            }
        } else {
            let users=site.query("SELECT id,nickname,picture,session_epoch,suspended FROM users WHERE discord_id=?",&[json!(discord_id)]).await?;
            if let Some(u) = users.first().filter(|u| u["suspended"] == 0) {
                new_session = json!({"user_id":u["id"],"user_nickname":u["nickname"],"user_picture":u["picture"],"sv":u["session_epoch"]});
            } else {
                new_session["flash_discord_error"] =
                    json!("먼저 사이트 계정에 디스코드를 연동해주세요.");
            }
        }
        let mut response = axum::response::Redirect::to("/").into_response();
        response
            .headers_mut()
            .insert("set-cookie", site.cookie(&new_session).parse().unwrap());
        return Ok(Some(response));
    }
    if matches!(
        path,
        "/api/videos/random_scroll" | "/api/videos/recommend_random"
    ) {
        return Ok(None);
    }
    if let Some(member) = path.strip_prefix("/api/videos/") {
        let member = percent_encoding::percent_decode_str(member).decode_utf8_lossy();
        let typ = params.get("type").map(String::as_str).unwrap_or("video");
        let page = params.get("pageToken").map(String::as_str).unwrap_or("");
        let members=site.query("SELECT member_name,channel_id,music_playlists,shorts_playlists,replay_playlists FROM members WHERE member_name=?",&[json!(member)]).await?;
        if members.is_empty()
            && !site.members["sidebar_generations"]
                .as_array()
                .unwrap()
                .iter()
                .any(|g| g["key"] == member.as_ref())
        {
            return Ok(Some(error(
                StatusCode::NOT_FOUND,
                "존재하지 않는 멤버입니다.",
            )));
        }
        let has_music = members
            .first()
            .and_then(|m| m["music_playlists"].as_str())
            .is_some_and(|s| s != "[]" && !s.is_empty());
        let has_replay = member == "칸나"
            || members
                .first()
                .and_then(|m| m["replay_playlists"].as_str())
                .is_some_and(|s| s != "[]" && !s.is_empty());
        if member == "칸나" && ["video", "shorts"].contains(&typ) {
            let folder = if typ == "video" {
                "1bnZXIH--kZxUSONIU3FlYNsAv2hmMDIV"
            } else {
                "1eN6H2F0PTyxemZS2yF4GYcESDBP_n_Jm"
            };
            let key = env("GOOGLE_DRIVE_API_KEY", "");
            let result = site
                .fetch_json(
                    "https://www.googleapis.com/drive/v3/files",
                    &[
                        ("q", format!("'{folder}' in parents and trashed=false")),
                        (
                            "fields",
                            "nextPageToken,files(id,name,thumbnailLink,createdTime)".into(),
                        ),
                        ("pageSize", "50".into()),
                        ("pageToken", page.into()),
                        ("key", key),
                    ],
                    90,
                )
                .await;
            let result = result.unwrap_or(json!({"files":[]}));
            let videos=result["files"].as_array().map(|files|files.iter().map(|f|json!({"videoId":f["id"],"id":f["id"],"title":f["name"],"thumbnail":format!("/api/drive_thumb/{}",f["id"].as_str().unwrap_or("")),"is_drive":true,"is_short":typ=="shorts","member_name":member,"publishedAt":f["createdTime"]})).collect::<Vec<_>>()).unwrap_or_default();
            return Ok(Some(output(
                json!({"videos":videos,"nextPageToken":result["nextPageToken"],"has_music":has_music,"has_replay":has_replay}),
            )));
        }
        if ["music", "replay"].contains(&typ) && !members.is_empty() {
            let column = if typ == "music" {
                "music_playlists"
            } else {
                "replay_playlists"
            };
            let mut lists = members[0][column]
                .as_str()
                .and_then(|s| serde_json::from_str::<Vec<String>>(s).ok())
                .unwrap_or_default();
            if member == "칸나" && typ == "replay" && lists.is_empty() {
                lists = [
                    "PLO1bVFkgUJ3dRBH3aBATeXC_cCjIZqMRX",
                    "PLO1bVFkgUJ3c6xz-mgmmD-nbE_baKCvZi",
                    "PLO1bVFkgUJ3fa1di4P9Faq0gqZH-R4GWG",
                ]
                .iter()
                .map(|s| s.to_string())
                .collect();
            }
            let mut videos = vec![];
            let mut next = Value::Null;
            for playlist in lists.iter().take(4) {
                if let Some(result) = site
                    .fetch_json(
                        "https://www.googleapis.com/youtube/v3/playlistItems",
                        &[
                            ("part", "snippet".into()),
                            ("playlistId", playlist.clone()),
                            ("maxResults", "50".into()),
                            ("pageToken", page.into()),
                            ("key", env("YOUTUBE_API_KEY", "")),
                        ],
                        90,
                    )
                    .await
                {
                    next = result["nextPageToken"].clone();
                    for item in result["items"].as_array().into_iter().flatten() {
                        let snippet = &item["snippet"];
                        let vid = &snippet["resourceId"]["videoId"];
                        videos.push(json!({"videoId":vid,"title":snippet["title"],"thumbnail":snippet["thumbnails"]["high"]["url"],"publishedAt":snippet["publishedAt"],"member_name":member,"is_drive":false,"is_short":false}));
                    }
                }
            }
            return Ok(Some(output(
                json!({"videos":videos,"nextPageToken":next,"has_music":has_music,"has_replay":has_replay}),
            )));
        }
        let generation = site.members["sidebar_generations"]
            .as_array()
            .unwrap()
            .iter()
            .find(|g| g["key"] == member.as_ref());
        let names = if let Some(g) = generation {
            g["members"]
                .as_array()
                .unwrap()
                .iter()
                .filter_map(|m| m["name"].as_str())
                .collect::<Vec<_>>()
        } else {
            vec![member.as_ref()]
        };
        if !members.is_empty() && params.get("sort").map(String::as_str) != Some("oldest") {
            let channel = members[0]["channel_id"].as_str().unwrap_or("");
            let short_lists = members[0]["shorts_playlists"]
                .as_str()
                .and_then(|s| serde_json::from_str::<Vec<String>>(s).ok())
                .unwrap_or_default();
            let list = if typ == "shorts" && !short_lists.is_empty() {
                short_lists[0].clone()
            } else if channel.len() == 24 && channel.starts_with("UC") {
                format!("UU{}", &channel[2..])
            } else {
                String::new()
            };
            if !list.is_empty() {
                if let Some(result) = site
                    .fetch_json(
                        "https://www.googleapis.com/youtube/v3/playlistItems",
                        &[
                            ("part", "snippet,contentDetails".into()),
                            ("playlistId", list),
                            ("maxResults", "50".into()),
                            ("pageToken", page.into()),
                            ("key", env("YOUTUBE_API_KEY", "")),
                        ],
                        120,
                    )
                    .await
                {
                    let mut fetched = vec![];
                    for item in result["items"].as_array().into_iter().flatten() {
                        let snippet = &item["snippet"];
                        let id = item["contentDetails"]["videoId"].as_str().unwrap_or("");
                        let title = snippet["title"].as_str().unwrap_or("");
                        if id.len() != 11 || matches!(title, "Private video" | "Deleted video") {
                            continue;
                        }
                        let lower = title.to_lowercase();
                        let short = typ == "shorts" && !short_lists.is_empty()
                            || videos
                                .iter()
                                .any(|v| v["videoId"] == id && v["is_short"] == true)
                            || lower.contains("shorts")
                            || title.contains("쇼츠");
                        let music = [
                            "cover", "커버", "mv", "m/v", "노래", "music", "음원", "song", "ost",
                        ]
                        .iter()
                        .any(|k| lower.contains(k));
                        if (typ == "shorts" && !short) || (typ != "shorts" && (short || music)) {
                            continue;
                        }
                        fetched.push(json!({"videoId":id,"id":id,"title":title,"thumbnail":format!("https://i.ytimg.com/vi_webp/{id}/hqdefault.webp"),"publishedAt":snippet["publishedAt"],"member_name":member,"is_drive":false,"is_short":short}));
                    }
                    return Ok(Some(output(
                        json!({"videos":fetched,"nextPageToken":result["nextPageToken"],"has_music":has_music,"has_replay":has_replay}),
                    )));
                }
            }
        }
        if params.get("sort").map(String::as_str) == Some("oldest") {
            let offset = page.parse::<i64>().unwrap_or(0).clamp(0, 20000);
            let placeholders = vec!["?"; names.len()].join(",");
            let mut args = names.iter().map(|n| json!(n)).collect::<Vec<_>>();
            args.push(json!(typ == "shorts"));
            args.push(json!(offset));
            let mut rows=site.query(&format!("SELECT video_id AS videoId,video_id AS id,title,thumbnail,member_name,is_short FROM member_video_archive WHERE member_name IN ({placeholders}) AND is_short=? ORDER BY id ASC LIMIT 51 OFFSET ?"),&args).await?;
            let more = rows.len() > 50;
            rows.truncate(50);
            for v in &mut rows {
                v["is_short"] = json!(v["is_short"] == 1);
            }
            return Ok(Some(output(
                json!({"videos":rows,"nextPageToken":if more{Some((offset+50).to_string())}else{None},"has_music":has_music,"has_replay":has_replay}),
            )));
        }
        let offset = page.parse::<usize>().unwrap_or(0).min(10000);
        let mut videos = videos
            .iter()
            .filter(|v| names.iter().any(|name| v["member_name"] == *name))
            .filter(|v| {
                if typ == "shorts" {
                    v["is_short"] == true
                } else {
                    v["is_short"] != true
                }
            })
            .cloned()
            .collect::<Vec<_>>();
        if params.get("sort").map(String::as_str) == Some("popular") {
            videos.sort_by_key(|v| std::cmp::Reverse(v["viewCount"].as_u64().unwrap_or(0)));
        }
        let count = videos.len();
        let videos = videos.into_iter().skip(offset).take(50).collect::<Vec<_>>();
        return Ok(Some(output(
            json!({"videos":videos,"nextPageToken":if offset+50<count{Some((offset+50).to_string())}else{None},"has_music":has_music,"has_replay":has_replay}),
        )));
    }
    if let Some(vid) = path.strip_prefix("/api/youtube_comments/") {
        let result = site
            .fetch_json(
                "https://www.googleapis.com/youtube/v3/commentThreads",
                &[
                    ("part", "snippet,replies".into()),
                    ("videoId", vid.into()),
                    ("maxResults", "30".into()),
                    ("order", "relevance".into()),
                    (
                        "pageToken",
                        params.get("pageToken").cloned().unwrap_or_default(),
                    ),
                    ("key", env("YOUTUBE_API_KEY", "")),
                ],
                120,
            )
            .await
            .unwrap_or(json!({"items":[]}));
        let comments=result["items"].as_array().into_iter().flatten().map(|c|{let s=&c["snippet"]["topLevelComment"]["snippet"];json!({"id":c["id"],"author":s["authorDisplayName"],"nickname":s["authorDisplayName"],"picture":s["authorProfileImageUrl"],"content":s["textOriginal"],"like_count":s["likeCount"],"date":s["publishedAt"],"reply_count":c["snippet"]["totalReplyCount"]})}).collect::<Vec<_>>();
        return Ok(Some(output(
            json!({"success":true,"comments":comments,"nextPageToken":result["nextPageToken"]}),
        )));
    }
    if let Some(id) = path
        .strip_prefix("/api/drive_thumb/")
        .or_else(|| path.strip_prefix("/api/drive_video_title/"))
    {
        if id.len() < 20
            || id.len() > 100
            || !id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "_-".contains(c))
        {
            return Ok(Some(error(StatusCode::BAD_REQUEST, "잘못된 파일입니다.")));
        }
        let fields = if path.contains("drive_thumb") {
            "thumbnailLink"
        } else {
            "name"
        };
        let result = site
            .fetch_json(
                &format!("https://www.googleapis.com/drive/v3/files/{id}"),
                &[
                    ("fields", fields.into()),
                    ("key", env("GOOGLE_DRIVE_API_KEY", "")),
                ],
                300,
            )
            .await;
        if path.contains("drive_thumb") {
            if let Some(link) = result
                .as_ref()
                .and_then(|r| r["thumbnailLink"].as_str())
                .filter(|link| {
                    reqwest::Url::parse(link)
                        .ok()
                        .and_then(|u| u.host_str().map(str::to_string))
                        .is_some_and(|host| host.ends_with(".googleusercontent.com"))
                })
            {
                return Ok(Some(
                    axum::response::Redirect::temporary(link).into_response(),
                ));
            }
            return Ok(Some(
                axum::response::Redirect::temporary("/static/logo/logo.webp").into_response(),
            ));
        }
        return Ok(Some(output(
            json!({"success":result.is_some(),"title":result.as_ref().map(|r|r["name"].clone()).unwrap_or(Value::Null)}),
        )));
    }
    let _ = headers;
    Ok(None)
}
