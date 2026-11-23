"""Read the existing Final_V1_Team JSON API without starting a service or research job."""
import http.client
import json
import math
import re
from urllib.parse import urlencode, urlsplit


class ServiceReadError(RuntimeError):
    """An HTTP/service failure, separate from missing observations in a flight."""

    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.status = status


class ServiceReader:
    """Stateless loopback reads. Returned dictionaries retain the service's schema."""

    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: float = 30):
        url = urlsplit(base_url)
        if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or url.username is not None or url.password is not None
                or url.path not in ("", "/") or url.query or url.fragment):
            raise ValueError("Use the loopback HTTP root of the existing dashboard service.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Service timeout must be finite and positive.")
        self.host = url.hostname
        self.port = url.port
        self.timeout = timeout

    def _get(self, path, params=None) -> dict:
        if params:
            values = {key: value for key, value in params.items() if value is not None}
            path += "?" + urlencode(values)
        return self._request("GET", path)

    def _request(self, method, path, payload=None) -> dict:
        # Direct connections avoid system proxies and never follow HTTP redirects.
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            headers = {"Accept": "application/json"}
            body = None
            if payload is not None:
                headers["Content-Type"] = "application/json"
                body = json.dumps(payload, allow_nan=False).encode("utf-8")
            connection.request(method, "/api/" + path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise ServiceReadError("service_unavailable", str(exc)) from exc
        finally:
            connection.close()
        if response.headers.get_content_type() != "application/json":
            raise ServiceReadError("unexpected_content_type", response.reason, response.status)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ServiceReadError("invalid_json_response", str(exc), response.status) from exc
        if not isinstance(data, dict):
            raise ServiceReadError("invalid_response_shape", "Expected a JSON object", response.status)
        if not 200 <= response.status < 300:
            error = data.get("error")
            if isinstance(error, dict):
                raise ServiceReadError(error.get("code", "http_error"), error.get("message", response.reason), response.status)
            raise ServiceReadError("http_error", response.reason, response.status)
        return data

    def _run(self, run_id, revision, resource, **params) -> dict:
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-z0-9_-]{8,80}", run_id):
            raise ValueError("Invalid run_id for the dashboard service.")
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("An explicit bundle_revision is required.")
        return self._get(f"runs/{run_id}/{resource}", {"revision": revision, **params})

    def health(self) -> dict:
        return self._get("health")

    def runs(self) -> dict:
        return self._get("runs")

    def status(self) -> dict:
        return self._get("registry/status")

    def summaries(self) -> dict:
        return self._get("summaries")

    def jobs(self) -> dict:
        return self._get("jobs")

    def manifest(self, run_id, revision) -> dict:
        return self._run(run_id, revision, "manifest")

    def scene(self, run_id, revision) -> dict:
        return self._run(run_id, revision, "scene")

    def events(self, run_id, revision, *, start_s=None, end_s=None, event_type=None,
               limit=10000, offset=0) -> dict:
        return self._run(run_id, revision, "events", **{
            "from": start_s, "to": end_s, "type": event_type, "limit": limit, "offset": offset,
        })

    def timeline(self, run_id, revision, *, start_s, end_s, at_s,
                 observer=None, peer=None, channels=None, request_id=None) -> dict:
        if channels is not None and not isinstance(channels, str):
            channels = ",".join(channels)
        return self._run(run_id, revision, "timeline", **{
            "from": start_s, "to": end_s, "at": at_s, "observer": observer,
            "peer": peer, "channels": channels, "request_id": request_id,
        })


class ServiceOperations(ServiceReader):
    """Explicit, opt-in job requests to the existing loopback service."""

    def start_job(self, command, checkpoint=None):
        if command not in ("verify", "doctor", "evaluate"):
            raise ValueError("허용되지 않은 작업입니다.")
        payload = {"command": command}
        if command == "evaluate":
            payload.update(cases=1, workers=1)
            if checkpoint:
                payload["checkpoint"] = checkpoint
        return self._request("POST", "jobs", payload)

    def stop_job(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("작업 식별자를 확인하세요.")
        return self._request("POST", f"jobs/{job_id}/stop", {})
