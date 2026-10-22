use super::*;
use argon2::{Argon2, PasswordHash, PasswordHasher, PasswordVerifier, password_hash::SaltString};
use subtle::ConstantTimeEq;
static PASSWORD_WORK: std::sync::LazyLock<Arc<tokio::sync::Semaphore>> =
    std::sync::LazyLock::new(|| Arc::new(tokio::sync::Semaphore::new(1)));
async fn password_job<T: Send + 'static>(
    job: impl FnOnce() -> T + Send + 'static,
) -> Result<T, ()> {
    let permit = PASSWORD_WORK.clone().try_acquire_owned().map_err(|_| ())?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        job()
    })
    .await
    .map_err(|_| ())
}

pub fn admin(user: &Value) -> bool {
    let nickname = env("ADMIN_NICKNAME", "");
    let email = env("ADMIN_DRIVE_ALLOWED_EMAIL", "");
    !nickname.is_empty()
        && !email.is_empty()
        && user["nickname"] == nickname
        && user["email"]
            .as_str()
            .is_some_and(|s| s.eq_ignore_ascii_case(&email))
}
pub fn verify(hash: &str, password: &str) -> bool {
    if hash.starts_with("$argon2") {
        return PasswordHash::new(hash).ok().is_some_and(|h| {
            Argon2::default()
                .verify_password(password.as_bytes(), &h)
                .is_ok()
        });
    }
    let fields: Vec<_> = hash.split('$').collect();
    if fields.len() != 3 {
        return false;
    }
    let method: Vec<_> = fields[0].split(':').collect();
    if method.len() < 2 || method[0] != "pbkdf2" || method[1] != "sha256" {
        return false;
    }
    let rounds = method
        .get(2)
        .and_then(|n| n.parse::<u32>().ok())
        .unwrap_or(600000);
    if rounds > 2000000 {
        return false;
    }
    let result =
        pbkdf2::pbkdf2_hmac_array::<Sha256, 32>(password.as_bytes(), fields[1].as_bytes(), rounds);
    let actual = result
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    actual.as_bytes().ct_eq(fields[2].as_bytes()).into()
}
pub fn hash_password(password: &str) -> Option<String> {
    let salt = SaltString::generate(&mut rand::rngs::OsRng);
    let params = argon2::Params::new(65536, 3, 4, Some(32)).ok()?;
    Argon2::new(argon2::Algorithm::Argon2id, argon2::Version::V0x13, params)
        .hash_password(password.as_bytes(), &salt)
        .ok()
        .map(|v| v.to_string())
}
pub async fn api(
    site: &Site,
    path: &str,
    method: &str,
    headers: &HeaderMap,
    session: &Value,
    body: &Value,
) -> Result<Option<Response>, sqlx::Error> {
    if method != "POST"
        || !matches!(
            path,
            "/api/login" | "/api/register" | "/api/account/set-password"
        )
    {
        return Ok(None);
    }
    if PASSWORD_WORK.available_permits() == 0 {
        return Ok(Some(error(
            StatusCode::TOO_MANY_REQUESTS,
            "잠시 후 다시 시도해주세요.",
        )));
    }
    let existing_id = if path == "/api/account/set-password" {
        session["user_id"].as_i64().unwrap_or(0)
    } else {
        0
    };
    let existing = if existing_id != 0 {
        site.query(
            "SELECT login_id,nickname FROM users WHERE id=?",
            &[json!(existing_id)],
        )
        .await?
        .into_iter()
        .next()
        .unwrap_or(json!({}))
    } else {
        json!({})
    };
    let login = body["login_id"]
        .as_str()
        .or_else(|| existing["login_id"].as_str())
        .unwrap_or("")
        .trim();
    let nickname = body["nickname"]
        .as_str()
        .or_else(|| existing["nickname"].as_str())
        .unwrap_or("")
        .trim();
    let password = body["password"].as_str().unwrap_or("");
    let ip = headers
        .get("cf-connecting-ip")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("127.0.0.1");
    if login.is_empty() || login.len() > 30 || password.is_empty() || password.len() > 1024 {
        return Ok(Some(error(
            StatusCode::BAD_REQUEST,
            "아이디와 비밀번호를 입력해주세요.",
        )));
    }
    let user = if path == "/api/login" {
        let users=site.query("SELECT id,nickname,picture,password_hash,suspended,session_epoch FROM users WHERE login_id=?",&[json!(login)]).await?;
        let Some(user) = users.first() else {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "아이디 또는 비밀번호가 올바르지 않습니다.",
            )));
        };
        let hash = user["password_hash"].as_str().unwrap_or("").to_string();
        let pass = password.to_string();
        if !password_job(move || verify(&hash, &pass))
            .await
            .unwrap_or(false)
        {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "아이디 또는 비밀번호가 올바르지 않습니다.",
            )));
        }
        if user["suspended"] == 1 {
            return Ok(Some(error(StatusCode::FORBIDDEN, "정지된 계정입니다.")));
        }
        site.execute(
            "UPDATE users SET last_ip=? WHERE id=?",
            &[json!(ip), user["id"].clone()],
        )
        .await?;
        site.execute(
            "INSERT INTO user_login_log (user_id,ip_address) VALUES (?,?)",
            &[user["id"].clone(), json!(ip)],
        )
        .await?;
        user.clone()
    } else {
        if !(4..=20).contains(&login.len())
            || !login.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
            || password.chars().count() < 8
            || nickname.is_empty()
            || nickname.chars().count() > 20
            || nickname
                .chars()
                .any(|c| c.is_control() || "<>`".contains(c))
        {
            return Ok(Some(error(
                StatusCode::BAD_REQUEST,
                "아이디·비밀번호·닉네임 형식을 확인해주세요.",
            )));
        }
        if path == "/api/register"
            && (env("STAFF_NICKNAMES", "")
                .split(',')
                .any(|n| n.trim() == nickname)
                || ["리도", "뱀늘이", "디자인담당", "개발자"]
                    .iter()
                    .any(|n| nickname.contains(n)))
        {
            return Ok(Some(error(
                StatusCode::BAD_REQUEST,
                "사용할 수 없는 닉네임입니다.",
            )));
        }
        let id = existing_id;
        if path == "/api/account/set-password" && id == 0 {
            return Ok(Some(error(
                StatusCode::UNAUTHORIZED,
                "로그인이 필요합니다.",
            )));
        }
        let duplicate = site
            .query(
                "SELECT id FROM users WHERE login_id=? AND id<>?",
                &[json!(login), json!(id)],
            )
            .await?;
        if !duplicate.is_empty() {
            return Ok(Some(error(
                StatusCode::CONFLICT,
                "이미 사용 중인 아이디입니다.",
            )));
        }
        if id != 0 {
            let rows = site
                .query("SELECT password_hash FROM users WHERE id=?", &[json!(id)])
                .await?;
            let old = rows
                .first()
                .and_then(|u| u["password_hash"].as_str())
                .unwrap_or("")
                .to_string();
            let pass = body["current_password"].as_str().unwrap_or("").to_string();
            if !old.is_empty()
                && !password_job(move || verify(&old, &pass))
                    .await
                    .unwrap_or(false)
            {
                return Ok(Some(error(
                    StatusCode::UNAUTHORIZED,
                    "현재 비밀번호가 올바르지 않습니다.",
                )));
            }
        }
        let pass = password.to_string();
        let Some(hash) = password_job(move || hash_password(&pass))
            .await
            .ok()
            .flatten()
        else {
            return Ok(Some(error(
                StatusCode::SERVICE_UNAVAILABLE,
                "비밀번호를 처리하지 못했습니다.",
            )));
        };
        let id = if id == 0 {
            site.execute(
                "INSERT INTO users (login_id,password_hash,nickname,last_ip) VALUES (?,?,?,?)",
                &[json!(login), json!(hash), json!(nickname), json!(ip)],
            )
            .await?
        } else {
            site.execute("UPDATE users SET login_id=?,password_hash=?,nickname=?,session_epoch=session_epoch+1 WHERE id=?",&[json!(login),json!(hash),json!(nickname),json!(id)]).await?;
            id as u64
        };
        site.query(
            "SELECT id,nickname,picture,session_epoch FROM users WHERE id=?",
            &[json!(id)],
        )
        .await?
        .remove(0)
    };
    let data = json!({"user_id":user["id"],"user_nickname":user["nickname"],"user_picture":user["picture"],"sv":user["session_epoch"]});
    let mut response =
        output(json!({"success":true,"nickname":user["nickname"],"picture":user["picture"]}));
    response
        .headers_mut()
        .insert("set-cookie", site.cookie(&data).parse().unwrap());
    Ok(Some(response))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn compatible_argon_hash() {
        let h = hash_password("migration-test-password").unwrap();
        assert!(verify(&h, "migration-test-password"));
        assert!(!verify(&h, "incorrect"));
    }
    #[test]
    fn rejects_bad_hash() {
        assert!(!verify("invalid", "pw"));
        assert!(!verify("pbkdf2:sha256:999999999$x$00", "pw"));
    }
}
