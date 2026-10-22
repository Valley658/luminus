use super::*;
pub fn start(site: Arc<Site>) {
    if env("SCHEDULER_ENABLED", "true") == "false" {
        return;
    }
    if let Some(mut receiver) = site.analytics_receiver.lock().unwrap().take() {
        let metrics = site.clone();
        tokio::spawn(async move {
            while let Some((path, ip)) = receiver.recv().await {
                let _ = metrics
                    .execute(
                        "INSERT INTO page_views (path,ip_address) VALUES (?,?)",
                        &[json!(path), json!(ip)],
                    )
                    .await;
            }
        });
    }
    let live = site.clone();
    tokio::spawn(async move {
        loop {
            refresh_live(&live).await;
            tokio::time::sleep(std::time::Duration::from_secs(20)).await;
        }
    });
    tokio::spawn(async move {
        loop {
            if refresh_videos(&site).await.is_err() {
                eprintln!("영상 목록 갱신 실패");
            }
            tokio::time::sleep(std::time::Duration::from_secs(1800)).await;
        }
    });
}
async fn refresh_live(site: &Site) {
    let mut status = serde_json::Map::new();
    let mut details = serde_json::Map::new();
    if let Some(members) = site.members["sidebar_members"].as_array() {
        for member in members {
            let name = member["name"].as_str().unwrap_or("");
            let id = member["chzzkId"].as_str().unwrap_or("");
            let value = site
                .fetch_json(
                    &format!("https://api.chzzk.naver.com/polling/v2/channels/{id}/live-status"),
                    &[],
                    15,
                )
                .await;
            let open = value
                .as_ref()
                .is_some_and(|v| v["content"]["status"] == "OPEN");
            status.insert(name.into(), json!(open));
            if open {
                if let Some(value) = site
                    .fetch_json(
                        &format!(
                            "https://api.chzzk.naver.com/service/v3/channels/{id}/live-detail"
                        ),
                        &[],
                        15,
                    )
                    .await
                {
                    let c = &value["content"];
                    let thumb = c["liveImageUrl"]
                        .as_str()
                        .filter(|s| s.starts_with("https://livecloud-thumb.akamaized.net/"))
                        .unwrap_or("")
                        .replace("{type}", "720");
                    details.insert(name.into(),json!({"title":c["liveTitle"],"category":c["liveCategoryValue"],"viewers":c["concurrentUserCount"],"thumbnail":thumb,"open_date":c["openDate"]}));
                }
            }
        }
    }
    *site.live.write().unwrap() = json!({"status":status,"details":details});
}
async fn refresh_videos(site: &Site) -> Result<(), sqlx::Error> {
    let key = env("YOUTUBE_API_KEY", "");
    if key.is_empty() {
        return Ok(());
    }
    let members=site.query("SELECT member_name,channel_id,shorts_playlists FROM members WHERE channel_id IS NOT NULL",&[]).await?;
    let mut videos = (*site.videos.load_full()).clone();
    for member in members {
        let name = member["member_name"].as_str().unwrap_or("");
        let channel = member["channel_id"].as_str().unwrap_or("");
        if !channel.starts_with("UC") || channel.len() != 24 {
            continue;
        }
        let mut lists = vec![(format!("UU{}", &channel[2..]), false)];
        if let Some(text) = member["shorts_playlists"].as_str() {
            if let Ok(Value::Array(shorts)) = serde_json::from_str(text) {
                for list in shorts.iter().take(4).filter_map(Value::as_str) {
                    lists.push((list.into(), true));
                }
            }
        }
        for (list, force_short) in lists {
            if let Some(result) = site
                .fetch_json(
                    "https://www.googleapis.com/youtube/v3/playlistItems",
                    &[
                        ("key", key.clone()),
                        ("part", "snippet,contentDetails".into()),
                        ("playlistId", list),
                        ("maxResults", "50".into()),
                    ],
                    300,
                )
                .await
            {
                if let Some(items) = result["items"].as_array() {
                    for item in items {
                        let snippet = &item["snippet"];
                        let id = item["contentDetails"]["videoId"].as_str().unwrap_or("");
                        if id.len() != 11 {
                            continue;
                        }
                        let title = snippet["title"].as_str().unwrap_or("");
                        if matches!(title, "Private video" | "Deleted video") {
                            videos.retain(|v|v["videoId"]!=id);
                            continue;
                        }
                        let short = force_short
                            || title.to_lowercase().contains("#shorts")
                            || title.contains("쇼츠");
                        let _=site.execute("INSERT INTO member_video_archive (video_id,member_name,channel_id,title,thumbnail,is_short) VALUES (?,?,?,?,?,?) ON DUPLICATE KEY UPDATE title=VALUES(title),thumbnail=VALUES(thumbnail),is_short=GREATEST(is_short,VALUES(is_short))",&[json!(id),json!(name),json!(channel),json!(title),json!(format!("https://i.ytimg.com/vi_webp/{id}/hqdefault.webp")),json!(short)]).await;
                        if let Some(existing) = videos.iter_mut().find(|v| v["videoId"] == id) {
                            existing["title"]=json!(title);
                            existing["thumbnail"]=json!(format!("https://i.ytimg.com/vi_webp/{id}/hqdefault.webp"));
                            if short {
                                existing["is_short"] = json!(true);
                            }
                            continue;
                        }
                        videos.push(json!({"videoId":id,"id":id,"title":title,"thumbnail":format!("https://i.ytimg.com/vi_webp/{id}/hqdefault.webp"),"member_name":name,"is_short":short,"publishedAt":snippet["publishedAt"],"is_drive":false}));
                    }
                }
            }
        }
    }
    videos.sort_by(|a, b| {
        b["publishedAt"]
            .as_str()
            .unwrap_or("")
            .cmp(a["publishedAt"].as_str().unwrap_or(""))
    });
    let mut counts = HashMap::<String, usize>::new();
    videos.retain(|v| {
        let count = counts
            .entry(v["member_name"].as_str().unwrap_or("").into())
            .or_default();
        *count += 1;
        *count <= 60
    });
    if !videos.is_empty() {
        if let Ok(data) = serde_json::to_vec(&videos) {
            let path = site.root.join("data/video_pool_cache.json");
            let temp = path.with_extension("json.tmp");
            if std::fs::write(&temp, data).is_ok() {
                let _ = std::fs::rename(temp, path);
            }
        }
        site.videos.store(Arc::new(videos));
    }
    Ok(())
}
