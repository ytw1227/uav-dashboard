import {esc,fmt,num} from './render.js';

const controlMetrics=[
 ['starts_per_h','시간당 새 지속 단절','회 / 시간'],
 ['cut_s_per_h','시간당 지속 단절 시간','초 / 시간'],
 ['timely_pdr','0.25초 내 전달률','ratio'],
 ['mean_relay_m','평균 중계기 이동 거리','m'],
];
const forecastMetrics=[
 ['recall','선행 탐지율','ratio'],
 ['precision','경보 정밀도','ratio'],
 ['false_or_late_per_fleet_h','오경보·늦은 경보','회 / 편대·시간'],
 ['average_precision','평균 정밀도 (AP)','ratio'],
];
function metrics(values,definitions){
 return '<div class="summary-metrics">'+definitions.map(([key,label,unit])=>
  '<div class="summary-value" data-metric="'+key+'"><span>'+esc(label)+'</span><strong>'+esc(fmt(values[key],unit,2))+'</strong></div>'
 ).join('')+'</div>';
}
export function renderFinalSummary(final){
 const control=final?.control,forecast=final?.forecast;
 const currentControl=control?.stats?.v21,currentForecast=forecast?.scores?.v21;
 if(final?.status!=='available'||!currentControl||!currentForecast){
  return '<div class="notice error">현재 V21의 저장 평가를 확인할 수 없습니다. 자료를 확인한 뒤 다시 읽어 주세요.</div>';
 }
 return '<p class="summary-policy">현재 채택 모델은 Final V1 · V21입니다. 아래는 저장된 평가 결과이며, 지도별 시연 결과와 별개입니다.</p>'+
  '<div class="summary-grid">'+
   '<article class="summary-card final-summary"><div class="summary-heading"><div><span class="tag teal">Final V1 · V21</span><h4>통신 유지·재배치 성능</h4></div><span class="tag muted">'+num(control.cases,0)+'개 조건</span></div>'+
   '<p class="summary-description">저장된 제어 평가 · 임무 완료 '+num(currentControl.completed,0)+' / '+num(currentControl.flights,0)+'</p>'+
   metrics(currentControl,controlMetrics)+
   '<p class="hint">지속 단절은 수색망이 1초 이상 끊긴 구간입니다. 새 단절 횟수는 시작부터 끊겨 있던 구간을 제외합니다. 전달률은 생성된 전체 패킷 중 0.25초 안에 전달된 비율입니다.</p></article>'+
   '<article class="summary-card final-summary"><div class="summary-heading"><div><span class="tag teal">V21 예측 평가</span><h4>수색망 단절 선행 예측</h4></div><span class="tag muted">'+num(forecast.flights,0)+'개 비행</span></div>'+
   '<p class="summary-description">단절 시작보다 3초 이상 7초 미만 앞선 경보를 평가합니다.</p>'+
   metrics(currentForecast,forecastMetrics)+
   '<p class="hint">저장된 기본정책 비행 관측 이력에서 평가한 V21 점수입니다. 지도에 표시되는 기본정책의 방향쌍 예측과는 다릅니다. 오경보·늦은 경보는 편대 전체의 비행 시간당 횟수입니다.</p></article>'+
  '</div>';
}
export function renderSummaryLibrary(data){
 return renderFinalSummary(data?.final_v1);
}
