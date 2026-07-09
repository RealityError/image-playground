# image-playground API

Base URL:

```text
http://<server-ip>:30116
```

## Rules

- Programmatic callers must use `/api/v1/*`.
- Browser UI calls use `/web/*` and require same-origin cookies plus internal web headers.
- Admin calls use `/admin/*`; the admin page path is configured by `ADMIN_PAGE_PATH` and `GET /admin` intentionally returns 404.
- API token is the same as the web space passphrase.
- Web and API calls share the same space, history ownership, block status, and concurrency quota.
- Upstream providers are configured by admins in the console. API callers may pass `provider_id` and `model`; unsupported provider parameters fail with `400`.
- Concurrent limit: 3 active generation/edit jobs per space.
- Generated files are saved locally and served through authenticated image routes.

## Auth

Use either header:

```http
Authorization: Bearer <your-space-passphrase>
```

or:

```http
X-API-Token: <your-space-passphrase>
```

## Health

```http
GET /healthz
```

Example:

```json
{
  "status": "ok",
  "time": "2026-05-12T12:00:00",
  "active_users": 1,
  "active_generations": 0
}
```

## Generate

```http
POST /api/v1/generate
Content-Type: application/json
Authorization: Bearer <your-space-passphrase>
```

Request:

```json
{
  "prompt": "生成一个极简海报，白底，中央一个红色方块",
  "n": 1,
  "size": "3840x2160",
  "quality": "high"
}
```

Supported fields:

- `prompt`: required.
- `provider_id`: optional. Uses the first enabled admin-configured upstream with an API Key when omitted.
- `model`: optional. Must be available on the selected upstream.
- `n`: optional, `1` to `8`.
- `size`: optional. Use `auto` or `WIDTHxHEIGHT`. Width and height must be multiples of 16, the longest edge can be up to `3840`, aspect ratio must not exceed `3:1`, and total pixels are capped at 4K level. Common values: `1024x1024`, `1536x1024`, `1024x1536`, `2048x2048`, `2048x1152`, `1152x2048`, `3840x2160`, `2160x3840`.
- `aspect_ratio`: optional alternative to `size`. Supported values: `auto`, `1:1`, `3:2`, `2:3`, `16:9`, `9:16`.
- `quality`: optional. Supported values: `auto`, `low`, `medium`, `high`, `standard`, `hd`.
- `response_format`: optional, `url` or `b64_json`.

Advanced fields such as `background`, `output_format`, `output_compression`, `partial_images`, `moderation`, `style`, and `user` are forwarded when present.

## Edit

```http
POST /api/v1/edit
Content-Type: multipart/form-data
Authorization: Bearer <your-space-passphrase>
```

Required form fields:

- `prompt`
- `image`

Optional form fields:

- `provider_id`
- `model`
- `image`: repeat this field to upload multiple input images.
- `mask`
- `n`
- `size`
- `aspect_ratio`
- `quality`
- `response_format`

Image order is preserved. The first uploaded `image` is passed as image 1, the second as image 2, and so on.

`mask` is forwarded unchanged to the upstream edit API. For OpenAI-compatible image editing, transparent mask areas indicate regions that may be edited; opaque areas are generally preserved. If `mask` is omitted, the model may edit the whole image.

Example:

```bash
curl -X POST "http://127.0.0.1:30116/api/v1/edit" \
  -H "Authorization: Bearer <your-space-passphrase>" \
  -F "prompt=把背景改成纯白，主体更清晰" \
  -F "image=@/path/to/image-1.png" \
  -F "image=@/path/to/image-2.png" \
  -F "mask=@/path/to/mask.png" \
  -F "size=1024x1024" \
  -F "quality=high"
```

## Success Response

`generate` and `edit` return the same shape:

```json
{
  "job_id": "20260512_120000_abcd1234",
  "created_at": "2026-05-12T12:00:00",
  "operation": "edit",
  "prompt": "把背景改成纯白，主体更清晰",
  "model": "gpt-image-2",
  "provider": {
    "id": "openai-main",
    "name": "OpenAI 主上游",
    "provider_type": "openai-compatible",
    "default_model": "gpt-image-2",
    "models": ["gpt-image-2"],
    "parameters": {
      "size": ["auto", "1024x1024"],
      "quality": ["auto", "high"]
    }
  },
  "request_params": {
    "model": "gpt-image-2",
    "provider_id": "openai-main",
    "provider_name": "OpenAI 主上游",
    "provider_type": "openai-compatible",
    "prompt": "把背景改成纯白，主体更清晰",
    "size": "1024x1024",
    "quality": "high"
  },
  "elapsed_seconds": 36.2,
  "image_count": 1,
  "scope": "api",
  "request_ip": "127.0.0.1",
  "images": [
    {
      "index": 1,
      "url": "/api/v1/images/20260512_120000_abcd1234/1",
      "saved_path": "/home/gpt-image-service/generated/20260512_120000_abcd1234_1.png",
      "size_bytes": 132044,
      "source": "remote_url_downloaded"
    }
  ]
}
```

## Fetch API Images

```http
GET /api/v1/images/{job_id}/{image_index}
Authorization: Bearer <your-space-passphrase>
```

## Web User Deletion

The web UI deletes user history through `/web/jobs/{job_id}/delete` or `/web/jobs/delete-batch`.

Deletion is soft-audited:

- The user no longer sees the job in web history.
- Local image files and thumbnails are removed when possible.
- SQLite keeps the prompt, IP, owner, time, route, params, and deletion metadata for admin audit.

## Common Errors

- `401 Invalid API token.`
- `403 Missing web request marker.`
- `409 Web client version mismatch. Refresh required.`
- `429 Too many concurrent generations for this space. Limit is 3.`
- `502 Image generate failed: ...`
- `502 Image edit failed: ...`
