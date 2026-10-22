use serde_json::Value;
use std::{
    net::IpAddr,
    sync::{Arc, RwLock},
    time::Duration,
};
#[derive(Clone, Copy)]
struct Network {
    address: IpAddr,
    bits: u32,
}
impl Network {
    fn parse(s: &str) -> Option<Self> {
        let (address, bits) = s.split_once('/')?;
        let address = address.parse::<IpAddr>().ok()?;
        let bits = bits.parse::<u32>().ok()?;
        if bits == 0 || bits > if address.is_ipv4() { 32 } else { 128 } {
            return None;
        }
        Some(Self { address, bits })
    }
    fn contains(self, ip: IpAddr) -> bool {
        match (self.address, ip) {
            (IpAddr::V4(a), IpAddr::V4(b)) => {
                u32::from(a) >> (32 - self.bits) == u32::from(b) >> (32 - self.bits)
            }
            (IpAddr::V6(a), IpAddr::V6(b)) => {
                u128::from(a) >> (128 - self.bits) == u128::from(b) >> (128 - self.bits)
            }
            _ => false,
        }
    }
}
fn networks(value: &Value) -> Vec<Network> {
    value["prefixes"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|p| {
            p["ipv4Prefix"]
                .as_str()
                .or_else(|| p["ipv6Prefix"].as_str())
                .and_then(Network::parse)
        })
        .collect()
}
pub struct Bots {
    ranges: RwLock<Vec<Vec<Network>>>,
}
impl Bots {
    pub fn new(site: Arc<crate::site::Site>) -> Arc<Self> {
        let feeds: Vec<Value> = serde_json::from_str(
            include_str!("../resources/bot-ip-ranges.json").trim_start_matches('\u{feff}'),
        )
        .unwrap_or_default();
        let bots = Arc::new(Self {
            ranges: RwLock::new(feeds.iter().map(networks).collect()),
        });
        let worker = bots.clone();
        tokio::spawn(async move {
            loop {
                for (index, feed) in feeds.iter().enumerate() {
                    if let Some(url) = feed["url"].as_str() {
                        let updated = async {
                            let response = site
                                .client
                                .get(url)
                                .timeout(Duration::from_secs(8))
                                .send()
                                .await?
                                .error_for_status()?;
                            // Never read an unbounded feed into memory.
                            let mut bytes = Vec::new();
                            use futures_util::StreamExt;
                            let mut stream = response.bytes_stream();
                            while let Some(chunk) = stream.next().await {
                                let chunk = chunk?;
                                if bytes.len() + chunk.len() > 512 * 1024 {
                                    return Ok::<_, reqwest::Error>(None);
                                }
                                bytes.extend_from_slice(&chunk);
                            }
                            Ok(serde_json::from_slice::<Value>(&bytes)
                                .ok()
                                .map(|v| networks(&v))
                                .filter(|r| !r.is_empty()))
                        }
                        .await;
                        if let Ok(Some(ranges)) = updated {
                            worker.ranges.write().unwrap()[index] = ranges;
                        }
                    }
                }
                // Classify historical visitors in bounded batches as well as new requests.
                let mut after = String::new();
                loop {
                    let rows=match site.query("SELECT DISTINCT ip_address AS ip FROM page_views WHERE ip_address > ? ORDER BY ip_address LIMIT 500",&[serde_json::json!(after)]).await {Ok(r)=>r,Err(_)=>break};
                    if rows.is_empty() {
                        break;
                    }
                    for row in &rows {
                        if let Some(raw) = row["ip"].as_str() {
                            after = raw.to_owned();
                            if let Ok(ip) = raw.parse() {
                                if worker.matches(ip) {
                                    let _ = site.auto_ban(ip, "자동 차단: 알려진 봇 IP").await;
                                }
                            }
                        }
                    }
                    tokio::time::sleep(Duration::from_millis(100)).await;
                }
                tokio::time::sleep(Duration::from_secs(24 * 60 * 60)).await;
            }
        });
        bots
    }
    pub fn matches(&self, ip: IpAddr) -> bool {
        self.ranges
            .read()
            .unwrap()
            .iter()
            .flatten()
            .any(|r| r.contains(ip))
    }
}
pub fn known_agent(agent: &str) -> bool {
    let agent = agent.to_ascii_lowercase();
    [
        "googlebot",
        "bingbot",
        "msnbot",
        "yandexbot",
        "baiduspider",
        "bytespider",
        "ahrefsbot",
        "semrushbot",
        "mj12bot",
        "dotbot",
        "gptbot",
        "claudebot",
        "ccbot",
        "petalbot",
        "applebot",
        "amazonbot",
        "facebookexternalhit",
        "twitterbot",
        "discordbot",
        "telegrambot",
        "slackbot",
        "sqlmap",
        "zgrab",
        "masscan",
        "nikto",
    ]
    .iter()
    .any(|name| agent.contains(name))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cidr_boundaries() {
        let r = Network::parse("203.0.113.16/28").unwrap();
        assert!(r.contains("203.0.113.16".parse().unwrap()));
        assert!(r.contains("203.0.113.31".parse().unwrap()));
        assert!(!r.contains("203.0.113.32".parse().unwrap()));
        assert!(!r.contains("2001:db8::1".parse().unwrap()));
        let v6 = Network::parse("2001:db8::/64").unwrap();
        assert!(v6.contains("2001:db8::ffff".parse().unwrap()));
        assert!(!v6.contains("2001:db8:1::1".parse().unwrap()));
        assert!(Network::parse("0.0.0.0/0").is_none());
    }
    #[test]
    fn browser_not_bot() {
        assert!(!known_agent("Mozilla/5.0 Chrome/141 Safari/537.36"));
        assert!(known_agent("Mozilla/5.0 (compatible; bingbot/2.0)"));
    }
    #[test]
    fn attack_paths() {
        assert!(attack_path("/static/%252eenv"));
        assert!(attack_path("/wp-admin/install.php"));
        assert!(attack_path("/.git/config"));
        assert!(!attack_path("/api/admin/site-settings"));
        assert!(!attack_path("/static/js/script.js"));
    }
}
pub fn attack_path(path: &str) -> bool {
    let mut decoded = path.to_owned();
    for _ in 0..2 {
        decoded = percent_encoding::percent_decode_str(&decoded)
            .decode_utf8_lossy()
            .into_owned();
    }
    let path = decoded.replace('\\', "/").to_ascii_lowercase();
    let segments: Vec<_> = path.split('/').filter(|s| !s.is_empty()).collect();
    segments.iter().any(|s| {
        s.starts_with(".env")
            || [
                ".git",
                ".svn",
                ".hg",
                "wp-admin",
                "wp-includes",
                "wp-content",
                "phpmyadmin",
                "pma",
                "cgi-bin",
                "actuator",
                "boaform",
                "hnap1",
            ]
            .contains(s)
            || s.ends_with(".php")
            || s.ends_with(".asp")
            || s.ends_with(".aspx")
    }) || path.contains("/vendor/phpunit/")
        || path.contains("/autodiscover/autodiscover.xml")
        || path == "/server-status"
        || segments.contains(&"..")
}
