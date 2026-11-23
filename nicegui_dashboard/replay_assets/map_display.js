import {UavMapRenderer as PlotlyMap, visualMapConfig} from './main_plotly.js';
import {MapRenderer} from './render.js';
import {commAt} from './comm_state.js';

export {visualMapConfig};

function supportsWebGL() {
    const canvas = document.createElement('canvas');
    try {
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (!gl) return false;
        gl.getExtension('WEBGL_lose_context')?.loseContext();
        return true;
    } catch {
        return false;
    }
}

function showNotice(before, id, message) {
    const note = document.createElement('p');
    note.id = id;
    note.className = 'replay-graphics-note';
    note.setAttribute('role', 'note');
    note.textContent = message;
    before.before(note);
}

class CanvasMap extends MapRenderer {
    constructor(canvas, onSelect) {
        super(canvas, onSelect);
        this.wrap = canvas.parentElement;
        canvas.closest('.uav-replay').dataset.graphics = 'canvas';
        showNotice(this.wrap, 'simpleMapNotice', '간이 지도 · 이 브라우저에서는 WebGL을 사용할 수 없어 저장된 기체 위치와 링크를 Canvas로 표시합니다. 3D 정밀 지도를 보려면 그래픽 가속을 지원하는 브라우저에서 다시 여세요.');
        showNotice(document.getElementById('incidentMapHost'), 'simpleIncidentNotice', '사건 3D 지도는 WebGL이 필요합니다. 사건 단계를 눌러 해당 시각의 연결 상태와 아래의 양방향 신호 그래프를 계속 확인할 수 있습니다.');
    }

    update(data) {
        data.communication = commAt(data.run, data.frame, data.time, data.selectedNode, data.selectedPeer);
        super.update(data);
        this.canvas.setAttribute('aria-label', `간이 지도 · 기체 ${this.hit.length}대 · ${data.time}초`);
        // The evidence panel is attached by the controller after update returns.
        queueMicrotask(() => {
            if (this.data === data && this.canvas.isConnected) {
                document.dispatchEvent(new CustomEvent('uav-map-frame', {detail: {data, renderedTime: data.time}}));
            }
        });
    }
}

export const UavMapRenderer = supportsWebGL() ? PlotlyMap : CanvasMap;
