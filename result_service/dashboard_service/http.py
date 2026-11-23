"""Loopback HTTP API and buildless dashboard assets."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs,unquote
from pathlib import Path
import gzip,hashlib,json,mimetypes,re,threading,uuid
from dashboard_contract.validation import read_json,confined
from . import query
from .summaries import list_summaries
from .common import metadata
from .sharing import create_snapshot,export_bundle
from .runner import JobManager

class LocalServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,registry,ui_root=None,runner=None):
        if address[0] not in ("127.0.0.1","localhost","::1"):raise ValueError("loopback_only")
        self.registry=registry
        self.ui_root=Path(ui_root or Path(__file__).resolve().parent.parent/"dashboard_ui").resolve()
        self.runner=runner or JobManager(registry.output/"jobs",registry.project,enabled=False,on_complete=lambda job:registry.rescan())
        self.downloads=registry.output/"downloads";self.downloads.mkdir(parents=True,exist_ok=True)
        self.share_lock=threading.Lock()
        super().__init__(address,Handler)

class Handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    server_version="UAVDashboard/1.0"
    def log_message(self,fmt,*args):pass

    def check_host(self):
        parsed=urlsplit("http://"+self.headers.get("Host",""))
        if parsed.hostname not in ("127.0.0.1","localhost","::1"):raise PermissionError("loopback_host_required")

    def send_bytes(self,data,status=200,content_type="application/json; charset=utf-8",cache=False,filename=None):
        etag='"'+hashlib.sha256(data).hexdigest()+'"'
        if cache and self.headers.get("If-None-Match")==etag:
            self.send_response(304);self.send_header("ETag",etag);self.send_header("Content-Length","0");self.end_headers();return
        encoded=False
        if len(data)>4096 and "gzip" in self.headers.get("Accept-Encoding","") and not filename:
            data=gzip.compress(data,compresslevel=3);encoded=True
        self.send_response(status);self.send_header("Content-Type",content_type)
        self.send_header("Content-Length",str(len(data)));self.send_header("Cache-Control","private, max-age=0, must-revalidate" if cache else "no-store")
        self.send_header("ETag",etag);self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if encoded:self.send_header("Content-Encoding","gzip");self.send_header("Vary","Accept-Encoding")
        if filename:self.send_header("Content-Disposition",'attachment; filename="'+filename+'"')
        self.end_headers();self.wfile.write(data)

    def send_json(self,obj,status=200,cache=False):
        self.send_bytes(json.dumps(obj,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf-8"),status,cache=cache)

    def failure(self,exc):
        status=403 if isinstance(exc,PermissionError) else 404 if isinstance(exc,(KeyError,FileNotFoundError)) else 400 if isinstance(exc,(ValueError,TypeError)) else 500
        code=getattr(exc,"code",str(exc).split(":")[0].strip("'")[:100])
        try:self.send_json({"error":{"code":code,"message":str(exc)[:1600]}},status)
        except (BrokenPipeError,ConnectionResetError):pass

    def do_GET(self):
        try:
            self.check_host();self.get()
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as exc:self.failure(exc)

    def get(self):
        parsed=urlsplit(self.path);path=unquote(parsed.path)
        params={k:v[-1] for k,v in parse_qs(parsed.query,keep_blank_values=True).items()}
        registry=self.server.registry
        if path=="/api/health":
            return self.send_json({"ok":True,"service_version":"1.1.0","project_name":registry.project.name,"mode":"completed_results_only","research_imports":False})
        if path=="/api/runs":return self.send_json(registry.list())
        if path=="/api/summaries":return self.send_json(list_summaries(registry))
        if path=="/api/registry/status":return self.send_json(registry.status())
        if path=="/api/jobs":return self.send_json({"jobs":self.server.runner.list_jobs(),"capabilities":self.server.runner.capabilities()})
        match=re.fullmatch(r"/api/runs/([a-z0-9_-]{8,80})/(manifest|scene|timeline|events)",path)
        if match:
            rid,action=match.groups();m,bundle=registry.get(rid,params.get("revision"))
            if action=="manifest":result=query.full_manifest(m,bundle)
            elif action=="scene":
                if not bundle:raise ValueError("raw_unavailable")
                result=read_json(bundle/"scene.json")
            elif action=="events":
                result=query.events(m,bundle,params.get("from"),params.get("to"),params.get("type"),
                                    params.get("limit",10000),params.get("offset",0))
            else:
                result=query.timeline(m,bundle,float(params.get("from",0)),float(params.get("to",min(60,m["simulation_end_s"]))),
                                      float(params.get("at",0)),params.get("observer"),params.get("peer"),params.get("channels"),params.get("request_id"))
            return self.send_json(result,cache=True)
        match=re.fullmatch(r"/api/downloads/([a-f0-9]{32}\.(?:html|zip))",path)
        if match:
            file=confined(self.server.downloads,match[1])
            if not file.is_file():raise FileNotFoundError("download_not_found")
            # Bounded bundle creation is validated before this streaming download.
            self.send_response(200);self.send_header("Content-Type","application/zip" if file.suffix==".zip" else "text/html; charset=utf-8")
            self.send_header("Content-Length",str(file.stat().st_size));self.send_header("Content-Disposition",'attachment; filename="'+file.name+'"')
            self.send_header("Cache-Control","no-store");self.send_header("X-Content-Type-Options","nosniff");self.end_headers()
            with file.open("rb") as stream:
                while data:=stream.read(1024*1024):self.wfile.write(data)
            return
        if path.startswith("/api/"):raise KeyError("endpoint_not_found")
        requested="index.html" if path in ("/","/index.html") else path.lstrip("/")
        file=(self.server.ui_root/requested).resolve()
        if not file.is_relative_to(self.server.ui_root) or file.suffix not in (".html",".css",".js",".svg",".ico",".woff2"):
            raise PermissionError("asset_path_not_allowed")
        if not file.is_file():raise FileNotFoundError("asset_not_found")
        kind=mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if file.suffix==".js":kind="text/javascript"
        return self.send_bytes(file.read_bytes(),content_type=kind+"; charset=utf-8",cache=True)

    def do_POST(self):
        try:
            self.check_host()
            origin=self.headers.get("Origin")
            if origin and origin!=f"http://{self.headers.get('Host')}":raise PermissionError("same_origin_required")
            if self.headers.get("Content-Type","").split(";")[0]!="application/json":raise ValueError("json_content_type_required")
            size=int(self.headers.get("Content-Length","0"))
            if not 0<=size<=32768:raise ValueError("request_too_large")
            data=json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(data,dict):raise ValueError("object_required")
            self.post(urlsplit(self.path).path,data)
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as exc:self.failure(exc)

    def post(self,path,data):
        registry=self.server.registry
        if path=="/api/registry/rescan":return self.send_json(registry.rescan(),202)
        if path=="/api/compare":return self.send_json(query.compare(registry,data.get("runs",[]),data.get("metric_ids")))
        if path=="/api/jobs":return self.send_json(self.server.runner.start(data),202)
        match=re.fullmatch(r"/api/jobs/([a-zA-Z0-9_-]+)/stop",path)
        if match:return self.send_json(self.server.runner.stop(match[1]),202)
        if path=="/api/share":
            if not self.server.share_lock.acquire(blocking=False):raise ValueError("share_already_running")
            try:
                rid=data.get("run_id");rev=data.get("revision",data.get("bundle_revision"))
                m,bundle=registry.get(rid,rev)
                if not bundle:raise ValueError("raw_unavailable")
                mode=data.get("format","snapshot")
                token=uuid.uuid4().hex
                if mode=="bundle":
                    target=self.server.downloads/(token+".zip")
                    result=export_bundle(bundle,target)
                elif mode=="snapshot":
                    t=float(data.get("time_s",0));window=data.get("window_s",15)
                    if isinstance(window,list):
                        if len(window)!=2:raise ValueError("invalid_snapshot_window")
                        a,b=map(float,window)
                        if b-a>240:raise ValueError("snapshot_window_limit_240s")
                    else:
                        radius=min(120,max(1,float(window)));a=max(0,t-radius);b=min(m["simulation_end_s"],t+radius)
                    if not a<=t<=b:raise ValueError("selection_outside_snapshot_window")
                    payload={"manifest":query.full_manifest(m,bundle),"scene":read_json(bundle/"scene.json"),
                             "timeline":query.timeline(m,bundle,a,b,t,observer=data.get("selected_node"),peer=data.get("selected_peer"),channels="positions,rf_frames,decisions,observations"),
                             "events":query.events(m,bundle,a,b),
                             "selection":{"time_s":t,"from_s":a,"to_s":b,"selected_event_id":data.get("selected_event_id"),"selected_node":data.get("selected_node"),"selected_peer":data.get("selected_peer"),"filters":data.get("filters",{})}}
                    target=self.server.downloads/(token+".html");result=create_snapshot(payload,target)
                else:raise ValueError("unsupported_share_format")
                return self.send_json(dict(result,download_url="/api/downloads/"+target.name))
            finally:self.server.share_lock.release()
        raise KeyError("endpoint_not_found")
