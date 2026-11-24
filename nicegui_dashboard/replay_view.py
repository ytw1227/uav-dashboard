"""NiceGUI's per-client replay surface, reusing the verified Sangam renderer modules."""
from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

from fastapi import HTTPException
from fastapi.responses import Response
from nicegui import app, ui

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).parent / 'vendor/sangam'
ASSETS = Path(__file__).parent / 'replay_assets'
BASE = '/part2/replay'
CSS = ('styles', 'flight_metrics', 'flight_radio', 'forecast_evaluation', 'incident_review',
       'incident_radio', 'main_plotly', 'incident_visual', 'presentation')


def _between(text: str, start: str, end: str) -> str:
    """Boundaries are function/section names in the recorded source, never line numbers."""
    first = text.index(start)
    return text[first:text.index(end, first + len(start))]


@lru_cache
def _markup() -> tuple[str, ...]:
    template = (SOURCE / 'index.html').read_text(encoding='utf-8-sig')
    return (
        _between(template, '<div id="notice"', '<nav class="tabs"'),
        _between(template, '<section id="analysisView">', '<section id="compareView"'),
        _between(template, '<section id="compareView"', '<section id="performanceView"'),
        _between(template, '<section id="performanceView"', '<footer class="main-footer">'),
        _between(template, '<dialog id="incidentDialog"', '<dialog id="jobsDialog"'),
        _between(template, '<div id="toast"', '<script type="module"'),
    )


@lru_cache
def _controller() -> str:
    """Compose the analysis controller from the S source.

    The native library, native navigation, local operations and network boot are
    owned by the NiceGUI page. Omit their legacy controllers and event bindings;
    keep the analytic sections and their original keyboard/camera/time logic.
    """
    code = (SOURCE / 'app.js').read_text(encoding='utf-8-sig')
    code = code.replace('const pair = pairedFlights(S.runs, row);',
                        'const pair = teamPair(row) || pairedFlights(S.runs, row);')
    code = code.replace('const pair = pairedFlights(S.runs, selectedCatalogRun());',
                        'const pair = teamPair(selectedCatalogRun()) || pairedFlights(S.runs, selectedCatalogRun());')
    code = code.replace("else $('flightTabNote').textContent = '';",
                        "else $('flightTabNote').textContent = teamPair(row) ? '기본 대형은 기존 비행 기록입니다.' : '';")
    code = code.replace('const model = isRepresentative(manifest)',
                        "const model = m.adapter_id === 'team-halfsec' || isRepresentative(manifest)")
    code = code.replace("$('runEyebrow').textContent = `",
                        "$('runEyebrow').textContent = m.adapter_id === 'team-halfsec' ? '팀 시뮬레이션 비행' : `")
    code = code.replace("$('revisionBadge').textContent = m.split",
                        "$('revisionBadge').textContent = m.team_commit ? '팀 코드 ' + m.team_commit.slice(0,7) : m.split")
    code = code.replace('S.manifest = manifest;', "S.manifest = manifest; if(S.tab==='performance')loadPerformance();")
    code = code.replace('const request=++performanceRequest;',
                        'const request=++performanceRequest;const model=teamManifest();'
                        "if(model)return teamModel(model,$('performanceContent'),()=>"
                        "request===performanceRequest&&S.tab==='performance'&&teamContext()===model.run_id&&"
                        "revisionOf(teamManifest())===revisionOf(model),metadata(selectedCatalogRun()).mode==='nominal');")
    code = code.replace(" $('pairedComparison').classList.toggle('hidden', !pair);",
                        " if(teamPair(row)){S.compareRequest=null;return teamComparison(request);}\n"
                        " $('pairedComparison').classList.toggle('hidden', !pair);")
    analytic = _between(code, 'function persistentRecoveryStats(', 'async function share(')
    analytic = analytic.replace('renderRuns();', 'publishSelection();')
    # Archival exports are managed by the local operations page. The shared
    # package's header 'shareButton' is absent from the replay surface.
    analytic = re.sub(r"\$\('shareButton'\)\.title=[^;]+;", '', analytic)
    return '\n'.join([
        'import {teamContext,teamDescriptor,teamManifest,teamPair,setTeamFlight,readTeamComparison} from "./team_flight.js";',
        'import {teamModel} from "./team_model.js";',
        code[:code.index('async function api(')].replace("'./main_plotly.js'", "'./map_display.js'"),
        'const api = (url, options) => window.__PORTABLE__.api(url, options);',
        _between(code, 'function runAPI(', 'function serviceState('),
        _between(code, 'function selectedCatalogRun(', 'function isDemoLibrary('),
        _between(code, 'function flightLabel(', 'function representativeCards('),
        analytic,
        _between(code, "$('playButton').onclick=togglePlayback;", "$('shareButton').onclick=share;"),
        "$('performanceRefresh').onclick=loadPerformance;",
        _between(code, 'let last=0,lastDraw=0;', "$('runList').innerHTML=Array.from"),
        code[code.index("document.querySelectorAll('[data-map-view]').forEach(button=>"):],
        (ASSETS / 'controls.js').read_text(encoding='utf8'),
    ])


@lru_cache
def _flight_metrics() -> str:
    """Cancel a view's pending evaluation read when its run is reset or disposed."""
    code = (SOURCE / 'flight_metrics.js').read_text(encoding='utf-8-sig')
    hooks = {
        'const plotBusy=new Map();': 'const evaluationRead=createEvaluationRead();\n const plotBusy=new Map();',
        'await globalThis.__DASHBOARD_READ_CHUNK__(descriptor.chunk)':
            'await evaluationRead.read(() => globalThis.__DASHBOARD_READ_CHUNK__(descriptor.chunk))',
        'reset(){state=null;': 'reset(){evaluationRead.cancel();state=null;',
        'dispose(){resize.disconnect();': 'dispose(){evaluationRead.cancel();resize.disconnect();',
    }
    for original, replacement in hooks.items():
        if code.count(original) != 1:
            raise ValueError('공유본의 지역 평가 읽기 경계가 변경되었습니다: ' + original)
        code = code.replace(original, replacement)
    return "import {createEvaluationRead} from './evaluation_read.js';\n" + code


@lru_cache
def _stylesheet() -> str:
    sheets = [(SOURCE / (name + '.css')).read_text(encoding='utf-8-sig') for name in CSS]
    # Package styles (especially native inputs and flex rows) belong to the replay
    # subtree. The NiceGUI library and operations retain their own visual rules.
    css = '\n'.join(sheets).replace(':root', ':scope')
    css = re.sub(r'(?<![\w-])body(?=[{.\s,])', ':scope', css)
    return '@scope (.uav-replay){\n' + css + '\n}\n' + (ASSETS / 'surface.css').read_text(encoding='utf8')


@app.get(BASE + '/style.css', include_in_schema=False)
def replay_style():
    return Response(_stylesheet(), media_type='text/css')


@app.get(BASE + '/{source}/{name}', include_in_schema=False)
def replay_module(source: str, name: str):
    from .team_semantics import adapt
    if source not in {'S', 'B'}:
        raise HTTPException(404)
    if name == 'app.js':
        body = _controller()
    elif name == 'flight_metrics.js':
        body = _flight_metrics()
    elif name in {'bootstrap.js', 'map_display.js', 'evaluation_read.js', 'team_flight.js', 'team_readouts.js', 'team_model.js'}:
        body = (ASSETS / name).read_text(encoding='utf8')
    elif name == 'margin_data.js':
        body = f"export {{marginRecords, warningThresholds}} from '/part2/data/{source}/margins.js';"
    elif re.fullmatch(r'[a-z_0-9]+\.js', name) and (SOURCE / name).is_file():
        body = (SOURCE / name).read_text(encoding='utf-8-sig')
        if name == 'library.js':
            body = body.replace("return m.adapter_id==='final-v1'", "return m.adapter_id==='team-halfsec'||m.adapter_id==='final-v1'")
    else:
        raise HTTPException(404)
    return Response(adapt(name, body), media_type='text/javascript', headers={'Cache-Control': 'no-cache'})


@dataclass
class ReplayView:
    """One browser's navigation and selection. Camera and high-rate clocks stay local."""
    client: object
    source: str
    on_state: Callable | None = None
    state: dict = field(default_factory=dict)

    async def _command(self, method: str, *args):
        call = f'window.uavReplay.{method}({",".join(json.dumps(a) for a in args)})'
        return await self.client.run_javascript(
            f'await window.uavReplayReady; return await {call}', timeout=90)

    async def select_run(self, run_id: str, revision: str, ticket: int = 0):
        return await self._command('selectRun', run_id, revision, ticket)

    async def open_run(self, run_id: str, revision: str, ticket: int = 0):
        """A deliberate map-card click restarts the pinned flight at its beginning."""
        return await self._command('openRun', run_id, revision, ticket)

    async def open_team(self, region, pin, sha, mode, ticket, restart):
        return await self._command('openTeam', region, *pin, sha, mode, ticket, restart)

    async def set_tab(self, tab: str):
        if tab not in {'map', 'compare', 'performance'}:
            raise ValueError('Unknown replay tab')
        return await self._command('setTab', tab)

    async def set_mode(self, mode: str):
        if mode not in {'nominal', 'v21'}:
            raise ValueError('Unknown flight mode')
        return await self._command('setMode', mode)

    async def receive(self, event):
        payload = event.args
        if not isinstance(payload, dict) or payload.get('source') != self.source:
            return
        self.state = dict(payload)
        if self.on_state is not None:
            result = self.on_state(self.state)
            if inspect.isawaitable(result):
                await result


def render_replay(source: str, on_state=None, initial_run=None, initial_revision=None, *, defer_initial=False) -> ReplayView:
    if source not in {'S', 'B'}:
        raise ValueError('Replay requires the S or B package')
    view = ReplayView(ui.context.client, source, on_state)
    ui.add_head_html(f'<link rel="stylesheet" href="{BASE}/style.css">')
    if source == 'S':
        example = json.loads((SOURCE / 'baseline.json').read_text(encoding='utf8'))['example']
        import html
        ui.add_head_html('<meta name="incident-example" data-run="' + html.escape(example['run_id'], quote=True) +
                         '" content="' + html.escape(example['event_id'], quote=True) + '">')
    with ui.element('div').classes('uav-replay w-full min-w-0').props('id=uavReplaySurface data-load-state=boot'):
        ui.html('<div role="status" id="uavReplayStatus">저장된 비행 자료를 불러오는 중…</div>'
                '<button type="button" id="uavReplayReload" class="button compact" hidden>다시 불러오기</button>',
                sanitize=False)
        for fragment in _markup():
            # Trusted original UI templates from the fixed S release; never user input.
            ui.html(fragment, sanitize=False).classes('uav-replay-section')
    ui.on('uav_replay_state', view.receive)
    options = json.dumps({'source': source, 'run': initial_run, 'revision': initial_revision,
                          'deferInitial': defer_initial}, ensure_ascii=True)
    # The connection and the component DOM both exist before importing the renderer.
    view.client.run_javascript(f'''
        window.uavReplayReady = import("{BASE}/{source}/bootstrap.js").then(m => m.mount({options}));
        window.uavReplayReady.catch(error => {{
            const status = document.getElementById('uavReplayStatus');
            if (status) {{status.hidden = false; status.textContent = '비행 자료를 열 수 없습니다. ' + error.message;}}
            const retry = document.getElementById('uavReplayReload');
            if (retry) {{retry.hidden = false; retry.onclick = () => location.reload();}}
        }});
    ''')
    return view
