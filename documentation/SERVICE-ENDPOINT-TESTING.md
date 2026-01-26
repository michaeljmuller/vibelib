# Service Endpoint Testing

This document describes how to test the service layer API from the command line using curl.

## Authentication

The service layer requires a valid GitHub OAuth token in the Authorization header.
The easiest way to obtain a token for testing is using GitHub's Device Flow.

## Device Flow Authentication

### Step 1: Request Device Code

```bash
curl -s -X POST "https://github.com/login/device/code" \
  -H "Accept: application/json" \
  -d "client_id=YOUR_GITHUB_CLIENT_ID&scope=user:email"
```

Response:
```json
{
  "device_code": "xxxxx",
  "user_code": "ABCD-1234",
  "verification_uri": "https://github.com/login/device",
  "expires_in": 899,
  "interval": 5
}
```

### Step 2: Authorize in Browser

Open the `verification_uri` (https://github.com/login/device) and enter the `user_code`.

### Step 3: Poll for Token

After authorizing in the browser, request the access token:

```bash
curl -s -X POST "https://github.com/login/oauth/access_token" \
  -H "Accept: application/json" \
  -d "client_id=YOUR_GITHUB_CLIENT_ID&device_code=DEVICE_CODE_FROM_STEP_1&grant_type=urn:ietf:params:oauth:grant-type:device_code"
```

Response:
```json
{
  "access_token": "gho_xxxxxxxxxxxx",
  "token_type": "bearer",
  "scope": "user:email"
}
```

### Step 4: Use the Token

Include the token in the Authorization header:

```bash
curl -H "Authorization: Bearer gho_xxxxxxxxxxxx" \
  "http://localhost:5001/api/objects"
```

## API Endpoints

### List Objects

Returns S3 object keys from the library bucket.

```bash
# Default (100 objects)
curl -H "Authorization: Bearer TOKEN" \
  "http://localhost:5001/api/objects"

# With limit
curl -H "Authorization: Bearer TOKEN" \
  "http://localhost:5001/api/objects?limit=10"
```

Response:
```json
{
  "objects": ["file1.epub", "file2.m4b", ...],
  "count": 10
}
```

### Health Check

No authentication required.

```bash
curl "http://localhost:5001/api/health"
```

Response:
```json
{
  "status": "ok"
}
```

## Notes

- Tokens are cached by the service for 5 minutes to reduce GitHub API calls.
- The GitHub client ID is configured in `docker/.env`.
- Device Flow must be enabled in your GitHub OAuth App settings.
