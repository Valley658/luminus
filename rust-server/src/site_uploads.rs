use super::*;
use image::{ImageFormat, ImageReader};
use std::io::Cursor;
static IMAGE_WORK: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(1);
pub async fn parse(
    site: &Site,
    path: &str,
    content_type: &str,
    bytes: axum::body::Bytes,
) -> Result<Value, String> {
    if ![
        "/api/fanart/upload",
        "/api/me/profile",
        "/api/community/post",
    ]
    .contains(&path)
        && !path.starts_with("/api/comments/")
    {
        return Err("지원하지 않는 업로드입니다.".into());
    }
    let boundary =
        multer::parse_boundary(content_type).map_err(|_| "업로드 형식이 올바르지 않습니다.")?;
    let stream = futures_util::stream::once(async move { Ok::<_, std::io::Error>(bytes) });
    let constraints = multer::Constraints::new().size_limit(
        multer::SizeLimit::new()
            .whole_stream(8 * 1024 * 1024)
            .per_field(7 * 1024 * 1024),
    );
    let mut multipart = multer::Multipart::with_constraints(stream, boundary, constraints);
    let mut body = json!({});
    let mut images = vec![];
    let mut count = 0;
    while let Some(field) = multipart
        .next_field()
        .await
        .map_err(|_| "업로드를 읽지 못했습니다.")?
    {
        count += 1;
        if count > 16 {
            return Err("업로드 항목이 너무 많습니다.".into());
        }
        let name = field.name().unwrap_or("").to_string();
        if field.file_name().is_none() {
            let text = field
                .text()
                .await
                .map_err(|_| "업로드 항목을 읽지 못했습니다.")?;
            if text.len() > 20000 {
                return Err("입력 내용이 너무 깁니다.".into());
            }
            body[&name] = json!(text);
            continue;
        }
        if !["image", "images", "picture"].contains(&name.as_str()) {
            return Err("이미지 파일만 업로드할 수 있습니다.".into());
        }
        if images.len() >= 4 {
            return Err("이미지는 최대 4장입니다.".into());
        }
        let data = field
            .bytes()
            .await
            .map_err(|_| "이미지 파일을 읽지 못했습니다.")?;
        let root = site.root.clone();
        let dir = if path == "/api/fanart/upload" {
            "images"
        } else if path == "/api/me/profile" {
            "uploads/profiles"
        } else {
            "uploads/community"
        };
        let permit = IMAGE_WORK
            .acquire()
            .await
            .map_err(|_| "이미지를 처리하지 못했습니다.")?;
        let asset = tokio::task::spawn_blocking(move || {
            let _permit = permit;
            save_image(&root, dir, &data)
        })
        .await
        .map_err(|_| "이미지를 처리하지 못했습니다.")??;
        if name == "picture" {
            body["picture"] = json!(asset.0);
        } else {
            images.push(json!(asset.0));
            body["thumbnail_url"] = json!(asset.1);
        }
    }
    body["image_url"] = images.first().cloned().unwrap_or(Value::Null);
    body["images"] = json!(images);
    Ok(body)
}
fn save_image(root: &std::path::Path, dir: &str, bytes: &[u8]) -> Result<(String, String), String> {
    let format = image::guess_format(bytes).map_err(|_| "이미지 형식이 올바르지 않습니다.")?;
    if ![
        ImageFormat::Jpeg,
        ImageFormat::Png,
        ImageFormat::WebP,
        ImageFormat::Gif,
    ]
    .contains(&format)
    {
        return Err("JPG·PNG·WebP·GIF만 허용합니다.".into());
    }
    let mut reader = ImageReader::with_format(Cursor::new(bytes), format);
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(4096);
    limits.max_image_height = Some(4096);
    limits.max_alloc = Some(64 * 1024 * 1024);
    reader.limits(limits);
    let image = reader
        .decode()
        .map_err(|_| "이미지 크기 또는 파일을 확인해주세요.")?;
    let base = format!("image_{:032x}", rand::random::<u128>());
    let target = root.join("static").join(dir);
    std::fs::create_dir_all(&target).map_err(|_| "이미지를 저장하지 못했습니다.")?;
    let name = if format == ImageFormat::Gif {
        let name = format!("{base}.gif");
        std::fs::write(target.join(&name), bytes).map_err(|_| "이미지를 저장하지 못했습니다.")?;
        name
    } else {
        let name = format!("{base}.webp");
        image
            .thumbnail(1920, 1920)
            .save_with_format(target.join(&name), ImageFormat::WebP)
            .map_err(|_| "이미지를 저장하지 못했습니다.")?;
        name
    };
    let thumb = format!("{base}_thumb.jpg");
    let mut file =
        std::fs::File::create(target.join(&thumb)).map_err(|_| "썸네일을 저장하지 못했습니다.")?;
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut file, 80)
        .encode_image(&image.thumbnail(480, 480))
        .map_err(|_| "썸네일을 저장하지 못했습니다.")?;
    Ok((
        format!("/static/{dir}/{name}"),
        format!("/static/{dir}/{thumb}"),
    ))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_fake_image() {
        assert!(
            save_image(
                std::path::Path::new("."),
                "images",
                b"<html>not an image</html>"
            )
            .is_err()
        );
    }
    #[test]
    fn rejects_oversized_dimensions() {
        let image = image::DynamicImage::new_rgb8(4097, 1);
        let mut data = Cursor::new(Vec::new());
        image.write_to(&mut data, ImageFormat::Png).unwrap();
        assert!(save_image(std::path::Path::new("."), "images", data.get_ref()).is_err());
    }
}
