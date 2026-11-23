import {installTeamReader} from './team_flight.js';

function loadScript(url) {
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = url;
        script.onload = resolve;
        script.onerror = () => reject(new Error('화면 구성요소를 읽을 수 없습니다. ' + url));
        document.head.append(script);
    });
}

async function readJson(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error('저장 자료 조회 실패 (' + response.status + ')');
    return response.json();
}

export async function mount({source, run, revision, deferInitial=false}) {
    const resource = '/part2/data/' + source;
    const [catalog, figures] = await Promise.all([
        readJson(resource + '/catalog'),
        readJson(resource + '/chunk/flight-figures'),
        loadScript(resource + '/plotly.js'),
    ]);
    window.__NICEGUI_CATALOG__ = catalog;
    const series = document.createElement('script');
    series.type = 'application/json';
    series.id = 'flight-figures';
    series.textContent = JSON.stringify(figures);
    document.querySelector('.uav-replay').append(series);
    await loadScript(resource + '/adapter.js');
    installTeamReader(catalog);
    await import('./presentation.js');
    const controller = await import('./app.js');
    await controller.initialize(source, run, revision, deferInitial);
    document.querySelector('#uavReplayStatus').hidden = true;
    return true;
}
