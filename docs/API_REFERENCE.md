# IP WATCH AI - API Reference

Quick reference for all API endpoints.

## Base URL

```
Production: https://ipwatchai.com/api/v1
Development: http://localhost:8000/api/v1
```

## Authentication

All protected endpoints require JWT Bearer token:

```
Authorization: Bearer <access_token>
```

### Auth Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/register` | Register new user |
| POST | `/auth/login` | Login and get tokens |
| POST | `/auth/refresh` | Refresh access token |
| POST | `/auth/logout` | Logout (invalidate token) |
| GET | `/auth/me` | Get current user info |
| PUT | `/auth/change-password` | Change password |

#### Login Request

```json
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username=user@example.com&password=secret
```

#### Login Response

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

---

## Search Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/search` | Search trademarks |
| POST | `/search/multi` | Multi-trademark search |
| POST | `/search/agentic` | AI-powered smart search |
| GET | `/search/suggestions` | Autocomplete suggestions |

#### Search Request

```json
POST /search
{
  "query": "NIKE",
  "nice_classes": [25, 35],
  "search_type": "hybrid",
  "limit": 50,
  "offset": 0
}
```

#### Search Types

| Type | Description |
|------|-------------|
| `text` | Exact/fuzzy text matching |
| `semantic` | AI semantic similarity |
| `visual` | Image-based similarity |
| `hybrid` | Combined scoring (recommended) |

#### Search Response

```json
{
  "results": [
    {
      "id": "uuid",
      "application_no": "2024/123456",
      "name": "NIKE",
      "holder_name": "Nike Inc.",
      "nice_classes": [25, 35],
      "status": "Registered",
      "image_url": "/images/...",
      "scores": {
        "text_similarity": 0.95,
        "semantic_similarity": 0.87,
        "visual_similarity": 0.72,
        "combined_score": 0.88
      }
    }
  ],
  "total": 150,
  "page": 1,
  "limit": 50
}
```

---

## Risk Analysis Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/risk/analyze` | Full risk analysis |
| POST | `/risk/quick` | Quick risk check |
| POST | `/risk/batch` | Batch analysis |
| GET | `/risk/report/{id}` | Get saved report |
| GET | `/risk/report/{id}/pdf` | Download PDF report |

#### Risk Analysis Request

```json
POST /risk/analyze
{
  "brand_name": "ADIDAS",
  "nice_classes": [25, 35],
  "image": "base64_encoded_image",
  "holder_name": "My Company"
}
```

#### Risk Analysis Response

```json
{
  "risk_score": 78.5,
  "risk_level": "HIGH",
  "conflicts": [
    {
      "trademark": {
        "application_no": "2020/054321",
        "name": "ADIDAZ",
        "holder_name": "Competitor Corp"
      },
      "similarity_scores": {
        "text": 0.92,
        "semantic": 0.85,
        "visual": 0.65,
        "phonetic": 0.95
      },
      "conflict_reasons": [
        "High phonetic similarity",
        "Same Nice classes",
        "Visual resemblance"
      ]
    }
  ],
  "recommendations": [
    "Consider alternative brand name",
    "Conduct clearance search before filing"
  ]
}
```

---

## Watchlist Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/watchlist` | List watched brands |
| POST | `/watchlist` | Add brand to watchlist |
| GET | `/watchlist/{id}` | Get watchlist item |
| PUT | `/watchlist/{id}` | Update watchlist item |
| DELETE | `/watchlist/{id}` | Remove from watchlist |
| POST | `/watchlist/scan` | Trigger manual scan |
| GET | `/watchlist/alerts` | Get conflict alerts |

#### Add to Watchlist

```json
POST /watchlist
{
  "brand_name": "MY BRAND",
  "nice_classes": [9, 35, 42],
  "image": "base64_optional",
  "alert_threshold": 70
}
```

---

## Upload Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/upload` | Upload trademark list |
| POST | `/upload/detect-columns` | Detect file columns |
| POST | `/upload/with-mapping` | Upload with column mapping |
| GET | `/upload/status/{job_id}` | Check upload status |
| GET | `/upload/template` | Download Excel template |

#### Column Detection

```json
POST /upload/detect-columns
Content-Type: multipart/form-data

file: <excel_or_csv_file>
```

#### Response

```json
{
  "columns": ["Brand", "App No", "Classes", "Date"],
  "sample_data": [
    {"Brand": "ACME", "App No": "2024/001", ...}
  ],
  "auto_mappings": {
    "brand_name": "Brand",
    "application_no": "App No",
    "nice_classes": "Classes"
  },
  "required_fields": ["brand_name", "application_no", "nice_classes"]
}
```

#### Upload with Mapping

```json
POST /upload/with-mapping
Content-Type: multipart/form-data

file: <excel_or_csv_file>
mappings: {"brand_name": "Brand", "application_no": "App No", ...}
```

---

## Trademark Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/trademarks` | List trademarks |
| GET | `/trademarks/{id}` | Get trademark details |
| GET | `/trademarks/by-app-no/{app_no}` | Get by application number |
| GET | `/trademarks/{id}/history` | Get trademark history |
| GET | `/trademarks/{id}/similar` | Get similar trademarks |

#### Trademark Detail Response

```json
{
  "id": "uuid",
  "application_no": "2024/123456",
  "registration_no": "2024-123456",
  "name": "BRAND NAME",
  "holder": {
    "id": "uuid",
    "name": "Company Name",
    "address": "123 Main St",
    "city": "Istanbul"
  },
  "nice_classes": [
    {"number": 25, "description": "Clothing..."},
    {"number": 35, "description": "Advertising..."}
  ],
  "status": "Registered",
  "application_date": "2024-01-15",
  "registration_date": "2024-06-20",
  "expiry_date": "2034-01-15",
  "image_url": "/images/2024/123456.jpg",
  "bulletin_no": "2024/05"
}
```

---

## Statistics Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stats/overview` | Dashboard statistics |
| GET | `/stats/classes` | Nice class distribution |
| GET | `/stats/trends` | Filing trends over time |
| GET | `/stats/holders` | Top trademark holders |

#### Overview Response

```json
{
  "total_trademarks": 1500000,
  "total_holders": 450000,
  "recent_filings": 5234,
  "active_conflicts": 127,
  "database_updated": "2024-01-20T10:30:00Z"
}
```

---

## Live Scraper Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/scraper/search` | Live TurkPatent search |
| GET | `/scraper/trademark/{app_no}` | Fetch live details |
| POST | `/scraper/bulk` | Bulk live fetch |

#### Live Search Request

```json
POST /scraper/search
{
  "query": "BRAND",
  "search_type": "name",
  "nice_classes": [25]
}
```

---

## Bulletin Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/bulletins` | List bulletins |
| GET | `/bulletins/{no}` | Get bulletin details |
| POST | `/bulletins/process` | Process new bulletin |
| GET | `/bulletins/latest` | Get latest bulletin |

---

## Admin Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/admin/users` | List users |
| POST | `/admin/users` | Create user |
| PUT | `/admin/users/{id}` | Update user |
| DELETE | `/admin/users/{id}` | Delete user |
| GET | `/admin/jobs` | Background job status |
| POST | `/admin/reindex` | Trigger reindexing |
| GET | `/admin/health` | System health check |

---

## Health & System Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Basic health check |
| GET | `/health/detailed` | Detailed system status |
| GET | `/info` | API version info |

#### Health Response

```json
{
  "status": "healthy",
  "database": "connected",
  "redis": "connected",
  "gpu": "available",
  "models_loaded": true,
  "version": "2.1.0"
}
```

---

## Error Responses

All endpoints return standard error format:

```json
{
  "detail": "Error message",
  "error_code": "VALIDATION_ERROR",
  "field": "brand_name"
}
```

### HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 422 | Validation Error |
| 429 | Rate Limited |
| 500 | Server Error |

---

## Rate Limits

| Endpoint Type | Limit |
|---------------|-------|
| Search | 60/min |
| Risk Analysis | 30/min |
| Uploads | 10/min |
| Scraper | 20/min |
| General | 120/min |

---

## Pagination

List endpoints support pagination:

```
GET /trademarks?page=1&limit=50&sort=created_at&order=desc
```

Response includes:

```json
{
  "data": [...],
  "total": 1500000,
  "page": 1,
  "limit": 50,
  "pages": 30000
}
```

---

## Filtering

Most list endpoints support filtering:

```
GET /trademarks?status=Registered&nice_classes=25,35&holder_city=Istanbul
```

---

## WebSocket Endpoints

| Endpoint | Description |
|----------|-------------|
| `/ws/alerts` | Real-time conflict alerts |
| `/ws/jobs/{id}` | Job progress updates |

```javascript
const ws = new WebSocket('wss://ipwatchai.com/ws/alerts');
ws.onmessage = (event) => {
  const alert = JSON.parse(event.data);
  console.log('New conflict:', alert);
};
```
