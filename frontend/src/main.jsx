import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  MapPin, CarFront, ChevronRight, ArrowLeft, CheckCircle2, X, House, Ticket, Info,
  Plus, Minus, LocateFixed, Layers, Navigation, Wifi, WifiOff, AlertTriangle
} from 'lucide-react';
import './style.css';

const API = import.meta.env.VITE_API_BASE_URL || `http://${window.location.hostname}:5000`;
// 데이터 구조가 바뀌어서 저장 키 버전을 올림 (예전 데모 데이터와 섞이지 않도록)
const KEY = 'smart-parking-user-v3';
const ENTRANCE_KEY = 'smart-parking-entrance-demo-v3';

// 차량번호: 숫자 4자리 (서버와 동일한 규칙)
const PLATE_RULE = /^\d{4}$/;
const onlyDigits = v => String(v).replace(/\D/g, '').slice(0, 4);

const defaultEntrance = { barrier: 'closed', last_plate: '', match: null, authorized_space: null, entry_status: 'waiting' };

// 데모 모드 초기값: A1은 다른 사람이 예약 후 주차 완료한 상태
const blankSpace = id => ({ id, occupied: false, reserved: false, plate: '', entry_status: 'NONE', entered: false, alarm: 'NONE' });
const initial = ['A1', 'A2', 'A3', 'A4', 'A5'].map(id =>
  id === 'A1'
    ? { ...blankSpace(id), occupied: true, reserved: true, plate: '4821', entry_status: 'PARKED', entered: true }
    : blankSpace(id)
);

// '주차 완료'를 눌렀는데 자리가 비어 있을 때 경고 표시 시간 (서버와 같게)
const WRONG_SPOT_ALARM_MS = 10000;

// 서버 응답(GET /api/parking) → 화면에서 쓰는 형태
const clean = data => (Array.isArray(data) ? data : (data?.data || [])).map(p => ({
  id: String(p.space_id ?? p.id ?? ''),
  occupied: Boolean(p.is_occupied ?? p.occupied),
  reserved: Boolean(p.is_reserved ?? p.reserved),
  plate: String(p.car_number ?? p.plate ?? ''),
  entry_status: p.entry_status || 'NONE',
  entered: Boolean(p.entered),
  alarm: p.alarm || 'NONE'
})).filter(p => p.id);

// 서버 응답(GET /api/gate/status) → 입구 패널 형태
const cleanGate = g => ({
  barrier: String(g?.status || 'CLOSED').toLowerCase(),
  last_plate: g?.last_car_number || '',
  match: g?.match ?? null,
  authorized_space: g?.authorized_space ?? null,
  entry_status: g?.entry_status || 'waiting'
});

function readDemo() {
  try {
    const d = JSON.parse(localStorage.getItem(KEY));
    if (!Array.isArray(d) || !d.length) return initial;
    return initial.map(p => ({ ...p, ...(d.find(s => s.id === p.id) || {}) }));
  } catch { return initial; }
}

// 주차면 상태 (지도 색상·라벨 기준)
function status(p) {
  if (p.alarm && p.alarm !== 'NONE') return 'alarm';                 // 경고 중
  if (p.occupied && !p.reserved) return 'unreserved';                // 예약 없는 칸에 차
  if (p.reserved && p.entry_status === 'PARKING') return 'parking'; // 앱에서 '주차하기' 누름
  if (p.reserved && p.entry_status === 'PARKED') return 'parked';   // 앱에서 '주차 완료' 누름
  if (p.occupied) return 'occupied';
  if (p.reserved) return 'reserved';
  return 'available';
}
const labels = { available: '예약 가능', reserved: '예약됨', parking: '주차중', parked: '주차 완료', occupied: '차량 있음', unreserved: '미예약 주차', alarm: '경고' };
const alarmLabels = { UNAUTHORIZED: '무단 주차 경고', WRONG_SPOT: '자리 확인 필요' };
const alarmMessages = {
  UNAUTHORIZED: "예약된 자리에 '주차하기' 없이 차량이 들어왔어요.",
  WRONG_SPOT: '주차가 완료되지 않았습니다. 자리를 확인해주세요.'
};
const labelOf = p => status(p) === 'alarm' ? (alarmLabels[p.alarm] || '경고') : labels[status(p)];
const svgColors = {
  available: ['#bfeacc', '#2a9958'],
  reserved: ['#ffe4b5', '#d59e3e'],
  parking: ['#d6e8ff', '#3c7dd9'],
  parked: ['#d8e0e4', '#99aab2'],
  occupied: ['#d8e0e4', '#99aab2'],
  unreserved: ['#ffd6d6', '#d0464a'],
  alarm: ['#ffd6d6', '#d0464a']
};
const stageLabels = { RESERVED: '예약됨 · 주차 전', PARKING: '주차중', PARKED: '주차 완료' };

// 서버와 같은 규칙으로 체험 모드 상태 바꾸기 (상단 카메라 점유 결과)
function applyOccupancy(p, occupied) {
  if (occupied) {
    if (p.reserved && p.entry_status === 'RESERVED') return { ...p, occupied: true, alarm: 'UNAUTHORIZED' };
    if (p.entry_status === 'PARKING') return { ...p, occupied: true, alarm: 'NONE' };
    return { ...p, occupied: true };
  }
  return { ...p, occupied: false, alarm: p.alarm === 'UNAUTHORIZED' ? 'NONE' : p.alarm };
}

function gateMatchLabel(e) {
  if (e.entry_status === 'authorized') return '일치 · 입차 승인';
  if (e.entry_status === 'entered') return '일치 · 통과 완료';
  if (e.entry_status === 'denied') {
    if (e.match === false) return '미예약 차량 · 입차 거부';
    if (e.match === true) return '이미 입차한 차량';
    return '인식 불확실 · 재인식 필요';
  }
  return '확인 전';
}

// 서버 오류 메시지를 그대로 보여주기
async function failMessage(res, fallback) {
  try { const body = await res.json(); return body?.message || fallback; } catch { return fallback; }
}

function App() {
  const [spaces, setSpaces] = useState(readDemo);
  const [mine, setMine] = useState(() => { try { return JSON.parse(localStorage.getItem(KEY + '-mine')) || null; } catch { return null; } });
  const [entrance, setEntrance] = useState(() => { try { return { ...defaultEntrance, ...JSON.parse(localStorage.getItem(ENTRANCE_KEY)) }; } catch { return defaultEntrance; } });
  const [page, setPage] = useState('home'), [mode, setMode] = useState('demo'), [connected, setConnected] = useState(false);
  const [selected, setSelected] = useState(null), [plate, setPlate] = useState(''), [busy, setBusy] = useState(false), [notice, setNotice] = useState('');
  const [zoom, setZoom] = useState(1), [pan, setPan] = useState({ x: 0, y: 0 }), [activeZone, setActiveZone] = useState(null);
  const mapFrame = useRef(null);
  const drag = useRef(null), moved = useRef(false);

  const available = spaces.filter(p => status(p) === 'available').length;
  // 내 예약: 칸 번호뿐 아니라 차량번호까지 같아야 내 예약 (자동 해제 후 남이 같은 칸을 예약한 경우 구분)
  const mineSpace = spaces.find(p => p.id === mine?.id && p.reserved && p.plate === mine?.plate);
  const selectedSpace = spaces.find(p => p.id === selected);
  const plateValid = PLATE_RULE.test(plate);
  const alerts = spaces.filter(p => status(p) === 'alarm' || status(p) === 'unreserved');

  useEffect(() => { if (mode === 'demo') localStorage.setItem(KEY, JSON.stringify(spaces)); }, [spaces, mode]);
  useEffect(() => { localStorage.setItem(KEY + '-mine', JSON.stringify(mine)); }, [mine]);
  useEffect(() => { if (mode === 'demo') localStorage.setItem(ENTRANCE_KEY, JSON.stringify(entrance)); }, [entrance, mode]);

  // 출차·시간 만료로 예약이 끝나면 '내 예약' 정리
  useEffect(() => {
    if (!mine || mineSpace) return;
    if (mode === 'api' && !connected) return; // 서버 응답 전에는 판단하지 않음
    setMine(null);
    setNotice('예약이 종료됐어요.');
  }, [mine, mineSpace, mode, connected]);

  // 서버 모드: 3초마다 주차면·입구 상태 조회
  useEffect(() => {
    if (mode !== 'api') return;
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch(`${API}/api/parking`, { cache: 'no-store' });
        if (!r.ok) throw Error();
        const data = clean(await r.json());
        if (!data.length) throw Error();
        if (alive) { setSpaces(data); setConnected(true); }
        try {
          const gr = await fetch(`${API}/api/gate/status`, { cache: 'no-store' });
          if (gr.ok) { const g = await gr.json(); if (alive) setEntrance(cleanGate(g.data)); }
        } catch { /* 입구 상태 조회 실패 시 주차면 조회는 유지 */ }
      } catch { if (alive) setConnected(false); }
    };
    poll();
    const timer = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [mode]);

  async function refresh() {
    const update = await fetch(`${API}/api/parking`, { cache: 'no-store' });
    if (update.ok) setSpaces(clean(await update.json()));
  }

  function changeMode(m) {
    setMode(m); setConnected(false);
    if (m === 'demo') {
      setSpaces(readDemo());
      try { setEntrance({ ...defaultEntrance, ...JSON.parse(localStorage.getItem(ENTRANCE_KEY)) }); } catch { setEntrance(defaultEntrance); }
    } else setEntrance(defaultEntrance);
  }

  const zoneCenters = { A: [594, 451], B: [182, 142], C: [572, 142], D: [182, 432] };
  function focusZone(zone = 'A') {
    if (moved.current) return;
    const rect = mapFrame.current?.getBoundingClientRect();
    const w = rect?.width || 474, h = rect?.height || 350;
    const svgScale = Math.min(w / 780, h / 600);
    const [cx, cy] = zoneCenters[zone];
    const targetZoom = zone === 'A' ? 2.2 : 2.05;
    setZoom(targetZoom);
    setPan({ x: -(cx - 390) * svgScale * targetZoom, y: -(cy - 300) * svgScale * targetZoom });
    setActiveZone(zone); setPage('map'); setNotice('');
  }
  function resetMap() { setZoom(1); setPan({ x: 0, y: 0 }); setActiveZone(null); }

  function chooseSpot(id) {
    if (moved.current) return;
    const p = spaces.find(s => s.id === id);
    if (!p) return;
    if (status(p) === 'available') { setSelected(id); setNotice(''); }
    else if (mineSpace?.id === id) setPage('ticket');
    else setNotice(`${id}은(는) 현재 예약할 수 없어요.`);
  }

  async function reserve() {
    const p = selectedSpace, n = plate;
    if (!p || status(p) !== 'available') return;
    if (!PLATE_RULE.test(n)) { setNotice('차량번호는 숫자 4자리로 입력해 주세요. (예: 1234)'); return; }
    if (mineSpace) { setNotice('기존 예약을 취소한 뒤 다시 예약해 주세요.'); return; }
    setBusy(true);
    try {
      if (mode === 'api') {
        const r = await fetch(`${API}/api/reservations`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ space_id: p.id, car_number: n })
        });
        if (!r.ok) throw Error(await failMessage(r, '예약 요청에 실패했어요.'));
        await refresh();
      } else {
        if (spaces.some(s => s.reserved && s.plate === n)) throw Error(`해당 차량(${n})은 이미 다른 칸에 예약되어 있어요.`);
        setSpaces(old => old.map(s => s.id === p.id ? { ...s, reserved: true, plate: n, entry_status: 'RESERVED', entered: false, alarm: 'NONE' } : s));
        setEntrance(defaultEntrance);
      }
      setMine({ id: p.id, plate: n }); setSelected(null); setPlate(''); setPage('ticket'); setNotice('주차 예약이 완료됐어요.');
    } catch (e) { setNotice(e.message); } finally { setBusy(false); }
  }

  // 예약 취소: 입구 통과 전 + 주차 시작 전에만 / 그 뒤로는 '주차 종료'
  const canCancel = mineSpace && !mineSpace.occupied && !mineSpace.entered && mineSpace.entry_status === 'RESERVED';
  const canEnd = mineSpace && (mineSpace.entered || mineSpace.entry_status !== 'RESERVED');
  async function cancel() {
    if (!canCancel) return;
    setBusy(true);
    try {
      if (mode === 'api') {
        const r = await fetch(`${API}/api/reservations/${encodeURIComponent(mine.id)}`, { method: 'DELETE' });
        if (!r.ok) throw Error(await failMessage(r, '예약 취소에 실패했어요.'));
        await refresh();
      } else setSpaces(old => old.map(s => s.id === mine.id ? blankSpace(s.id) : s));
      setMine(null); setNotice('예약이 취소됐어요.');
    } catch (e) { setNotice(e.message); } finally { setBusy(false); }
  }

  // 주차하기 / 주차 완료 / 주차 종료 — 서버 모드는 API, 체험 모드는 같은 규칙으로 직접 처리
  async function callAction(action, fallback) {
    const r = await fetch(`${API}/api/reservations/${encodeURIComponent(mine.id)}/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ car_number: mine.plate })
    });
    const body = await r.json().catch(() => ({}));
    await refresh();
    if (!r.ok) throw Error(body?.message || fallback);
    return body?.message;
  }
  const updateMine = fn => setSpaces(old => old.map(s => s.id === mine.id ? fn(s) : s));

  async function startParking() {
    if (!mineSpace) return;
    setBusy(true);
    try {
      if (mode === 'api') setNotice(await callAction('start', '주차를 시작하지 못했어요.'));
      else {
        if (mineSpace.occupied) throw Error(`${mine.id} 자리에 이미 차량이 있습니다. 자리를 확인해주세요.`);
        updateMine(s => ({ ...s, entry_status: 'PARKING' }));
        setNotice(`${mine.id} 자리에 주차를 시작하세요. 주차 후 '주차 완료'를 눌러주세요.`);
      }
    } catch (e) { setNotice(e.message); } finally { setBusy(false); }
  }

  async function completeParking() {
    if (!mineSpace) return;
    setBusy(true);
    try {
      if (mode === 'api') setNotice(await callAction('complete', '주차 완료 처리에 실패했어요.'));
      else {
        if (!mineSpace.occupied) {
          const id = mine.id;
          updateMine(s => ({ ...s, alarm: 'WRONG_SPOT' }));
          setTimeout(() => setSpaces(old => old.map(s => s.id === id && s.alarm === 'WRONG_SPOT' ? { ...s, alarm: 'NONE' } : s)), WRONG_SPOT_ALARM_MS);
          throw Error(alarmMessages.WRONG_SPOT);
        }
        updateMine(s => ({ ...s, entry_status: 'PARKED', alarm: 'NONE' }));
        setNotice(`${mine.id} 주차가 완료되었습니다.`);
      }
    } catch (e) { setNotice(e.message); } finally { setBusy(false); }
  }

  async function endParking() {
    if (!canEnd) return;
    setBusy(true);
    try {
      if (mode === 'api') await callAction('end', '주차 종료에 실패했어요.');
      else updateMine(s => ({ ...blankSpace(s.id), occupied: s.occupied }));
      setMine(null); setNotice('주차가 종료되었습니다. 이용해 주셔서 감사합니다.');
    } catch (e) { setNotice(e.message); } finally { setBusy(false); }
  }

  // ── 체험 모드 시뮬레이션 (실제 카메라·센서 대신 버튼으로) ──
  function simulateEntrance(plateMatched = true) {
    if (mode !== 'demo' || !mineSpace) return;
    const other = mine.plate === '9999' ? '0000' : '9999';
    if (plateMatched && mineSpace.entered) {
      setEntrance(old => ({ ...old, barrier: 'closed', last_plate: mine.plate, match: true, entry_status: 'denied' }));
      setNotice('체험: 이미 입차 처리된 차량이라 차단봉을 열지 않아요.');
      return;
    }
    if (plateMatched) {
      setEntrance({ barrier: 'open', last_plate: mine.plate, match: true, authorized_space: mine.id, entry_status: 'authorized' });
      updateMine(s => ({ ...s, entered: true }));
      setNotice('체험: 초음파 감지 → 번호판 확인 → 입구 차단봉 개방');
    } else {
      setEntrance({ barrier: 'closed', last_plate: other, match: false, authorized_space: null, entry_status: 'denied' });
      setNotice(`체험: 미예약 차량(${other}) → 입차 거부`);
    }
  }
  function simulateGateClose() {
    if (mode !== 'demo') return;
    setEntrance(old => ({ ...old, barrier: 'closed', entry_status: old.entry_status === 'authorized' ? 'entered' : old.entry_status }));
    setNotice('체험: 초음파로 차량 통과 확인 → 입구 차단봉 닫힘');
  }
  function simulateMySpot() {
    if (mode !== 'demo' || !mineSpace) return;
    updateMine(s => applyOccupancy(s, !s.occupied));
  }
  // 내 자리가 아닌 빈칸 하나 (자리 착각 테스트용)
  const otherSpot = spaces.find(s => s.id !== mine?.id && !s.reserved);
  function simulateOtherSpot() {
    if (mode !== 'demo' || !otherSpot) return;
    setSpaces(old => old.map(s => s.id === otherSpot.id ? applyOccupancy(s, !s.occupied) : s));
  }

  function pointerDown(e) { if (e.target.closest('button')) return; drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }; moved.current = false; e.currentTarget.setPointerCapture(e.pointerId); }
  function pointerMove(e) { if (!drag.current) return; const dx = e.clientX - drag.current.x, dy = e.clientY - drag.current.y; if (Math.abs(dx) + Math.abs(dy) > 7) moved.current = true; setPan({ x: drag.current.px + dx, y: drag.current.py + dy }); }
  function pointerUp() { drag.current = null; setTimeout(() => { moved.current = false; }, 80); }
  function zoomAt(delta) { setZoom(z => Math.max(.8, Math.min(3.2, +(z + delta).toFixed(2)))); }

  return <div className="app">
    <header className="header">
      <div className="brand"><span className="brand-logo">P</span><div><strong>주차 예약 시스템</strong><small>PARKING SYSTEM</small></div></div>
      <span className={`connection ${mode === 'api' && !connected ? 'offline' : ''}`}><i />{mode === 'demo' ? '체험 모드' : connected ? '실시간 연결' : '연결 대기'}</span>
    </header>

    <main className="main">
      {page === 'home' && <>
        <div className="welcome"><span className="eyebrow"><MapPin size={14} /> 제1주차장</span><h1>주차할 곳을<br /><em>미리 찾아보세요.</em></h1><p>지도에서 빈 주차 공간을 확인하고 간편하게 예약하세요.</p></div>
        <div className="availability"><div><span>현재 예약 가능한 공간</span><strong>{available}<small> / {spaces.length}면</small></strong><p>{available ? '지금 예약할 수 있는 자리가 있어요' : '현재 예약 가능한 자리가 없어요'}</p></div><span className="availability-icon"><CarFront size={30} /></span></div>
        {alerts.length > 0 && <div className="alert-box"><AlertTriangle size={18} /><span>{alerts.map(p => `${p.id} ${labelOf(p)}`).join(' · ')}</span></div>}
        <div className="section-title"><h2>주차장 지도</h2><span>전체 구역 보기</span></div>
        <button className="map-teaser" onClick={() => { resetMap(); setPage('map'); }}><MiniMap spaces={spaces} /><div className="map-teaser-bottom"><div><strong>제1주차장</strong><small>A구역 · 예약 가능 {available}면</small></div><span className="round-arrow"><ChevronRight size={19} /></span></div></button>
        <button className="primary" onClick={() => focusZone('A')}>빈 주차 공간 찾기 <ChevronRight size={19} /></button>
        {mineSpace && <button className="my-shortcut" onClick={() => setPage('ticket')}><Ticket size={22} /><span><strong>예약 중인 주차 공간 {mine.id}</strong><small>내 예약 확인하기</small></span><ChevronRight size={18} /></button>}
      </>}

      {page === 'map' && <>
        <div className="map-head"><button className="back" onClick={() => setPage('home')} aria-label="홈으로"><ArrowLeft size={21} /></button><div><h1>주차장 지도</h1><p>지도를 움직이거나 확대해서 공간을 선택하세요.</p></div></div>
        <div className="map-summary"><div><span className="pulse" /> 제1주차장 · A구역 운영</div><strong>{available}면 <span>예약 가능</span></strong></div>
        <div className="map-frame" ref={mapFrame} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}>
          <div className="map-canvas" style={{ transform: `translate(${pan.x}px,${pan.y}px) scale(${zoom})` }}><ParkingMap spaces={spaces} onZone={focusZone} onSpot={chooseSpot} /></div>
          <div className="map-watermark"><Layers size={15} /> {activeZone ? `${activeZone}구역 확대 보기` : '제1주차장 전체 지도'}</div>
          <div className="map-controls"><button aria-label="확대" onClick={() => zoomAt(.3)}><Plus size={21} /></button><button aria-label="축소" onClick={() => zoomAt(-.3)}><Minus size={21} /></button><button aria-label="전체 지도" onClick={resetMap}><LocateFixed size={20} /></button></div>
        </div>
        <div className="legend"><span><i className="dot available" />예약 가능</span><span><i className="dot occupied" />주차 완료</span><span><i className="dot reserved" />예약됨</span><span><i className="dot parking" />주차중</span><span><i className="dot alarm" />경고·미예약</span><span><i className="dot inactive" />미운영</span></div>
        <div className="zone-tabs" aria-label="주차 구역 선택">{['A', 'B', 'C', 'D'].map(z => <button key={z} className={activeZone === z ? 'active' : ''} onClick={() => focusZone(z)}>{z}구역</button>)}</div>
        <div className="zone-card"><div><span className="eyebrow">{activeZone ? `${activeZone} ZONE` : 'PARKING LOT 1'}</span><h2>{activeZone ? `${activeZone}구역` : '전체 주차장'} <span>{!activeZone ? '· 4개 구역' : activeZone === 'A' ? '· 주차면 5개' : '· 예약 미지원'}</span></h2><p>{activeZone === 'A' ? '실시간 감지 및 예약이 가능한 구역이에요.' : activeZone ? '이 구역은 현재 지도 탐색만 지원해요.' : '지도에서 A·B·C·D 구역을 눌러 확대해 보세요.'}</p></div><button onClick={activeZone ? resetMap : () => focusZone('A')}>{activeZone ? '전체 보기' : 'A구역 확대'} <Navigation size={16} /></button></div>
        {(!activeZone || activeZone === 'A') && <div className="spot-list">{spaces.map(p => <button key={p.id} className="spot-row" onClick={() => chooseSpot(p.id)}><span className={`spot-square ${status(p)}`}><CarFront size={20} /></span><span><strong>{p.id}</strong><small>A구역</small></span><span className={`status-label ${status(p)}`}>{labelOf(p)}</span><ChevronRight size={17} /></button>)}</div>}
        <p className="map-note"><Info size={15} /> B·C·D구역은 예약 미지원입니다. A구역 5면만 예약할 수 있어요.</p>
      </>}

      {page === 'ticket' && <>
        <div className="page-head"><span className="eyebrow">MY RESERVATION</span><h1>내 예약</h1><p>입구에서 차량번호를 확인하고, 앱에서 주차하기 → 주차 완료 → 주차 종료 순서로 진행해요.</p></div>
        <div className="entrance-panel">
          <div className="entrance-title"><strong>주차장 입구 · 공용 차단봉</strong><span className={entrance.barrier === 'open' ? 'entrance-open' : 'entrance-closed'}>{entrance.barrier === 'open' ? '열림' : '닫힘'}</span></div>
          <div className="ticket-detail"><span>최근 인식 차량번호</span><strong>{entrance.last_plate || '인식 대기'}</strong></div>
          <div className="ticket-detail"><span>예약 차량번호 대조</span><strong>{gateMatchLabel(entrance)}</strong></div>
          <p className="hint">차단봉은 입구 OCR 인식과 서버 승인 결과에 따라 제어됩니다. 주차면별 차단봉은 없습니다.</p>
        </div>
        {mineSpace ? <div className="ticket-card">
          <div className="ticket-badge"><CheckCircle2 size={18} /> {stageLabels[mineSpace.entry_status] || '예약됨'}</div>
          <span>제1주차장 · A구역</span>
          <div className="ticket-spot">{mine.id}</div>
          <div className="ticket-detail"><span>예약 차량번호</span><strong>{mine.plate}</strong></div>
          <div className="ticket-detail"><span>입구 통과</span><strong>{mineSpace.entered ? '통과 완료' : '아직 안 함'}</strong></div>
          <div className="ticket-detail"><span>주차 단계</span><strong>{stageLabels[mineSpace.entry_status] || '-'}</strong></div>
          <div className="ticket-detail"><span>상단 카메라 (내 자리)</span><strong>{mineSpace.occupied ? '차량 감지' : '빈 자리'}</strong></div>
          {mineSpace.alarm !== 'NONE' && <div className="alert-box"><AlertTriangle size={18} /><span>{alarmMessages[mineSpace.alarm] || '경고'}</span></div>}

          {mineSpace.entry_status === 'RESERVED' && <>
            <button className="primary" disabled={busy || mineSpace.occupied} onClick={startParking}>주차하기 <ChevronRight size={18} /></button>
            <p className="hint">{mineSpace.occupied ? '자리에 이미 차량이 있어요. 자리를 확인해 주세요.' : '차를 자리에 대기 전에 먼저 눌러 주세요.'}</p>
          </>}
          {mineSpace.entry_status === 'PARKING' && <>
            <button className="primary" disabled={busy} onClick={completeParking}>주차 완료 <ChevronRight size={18} /></button>
            <p className="hint">차를 다 댄 뒤 눌러 주세요. 자리를 맞추느라 들어갔다 나와도 괜찮아요.</p>
          </>}
          {mineSpace.entry_status === 'PARKED' && <p className="hint">주차가 완료됐어요. 나갈 때 '주차 종료'를 눌러 주세요.</p>}

          {mode === 'demo' && <div className="demo-tools">
            <strong>체험 모드 · 카메라·센서 시뮬레이션</strong>
            <button className="demo-action" disabled={busy} onClick={() => simulateEntrance(true)}>입구: 예약 차량번호 인식 (일치)</button>
            <button className="demo-action" disabled={busy} onClick={() => simulateEntrance(false)}>입구: 다른 차량번호 인식 (미예약)</button>
            <button className="demo-action" disabled={busy || entrance.barrier !== 'open'} onClick={simulateGateClose}>초음파: 차량 통과 → 차단봉 닫기</button>
            <button className="demo-action" disabled={busy} onClick={simulateMySpot}>상단 카메라: {mine.id}에 차 {mineSpace.occupied ? '빠짐' : '들어옴'}</button>
            {otherSpot && <button className="demo-action" disabled={busy} onClick={simulateOtherSpot}>상단 카메라: {otherSpot.id}에 차 {otherSpot.occupied ? '빠짐' : '들어옴'} (자리 착각)</button>}
          </div>}
          {mode === 'demo' && <p className="hint">체험 모드에서는 실제 카메라·모터·센서가 작동하지 않아요.</p>}

          {canCancel && <button className="cancel" disabled={busy} onClick={() => { if (window.confirm('예약을 취소할까요?')) cancel(); }}>{busy ? '처리 중...' : '예약 취소하기'}</button>}
          {canEnd && <button className="cancel" disabled={busy} onClick={() => { if (window.confirm('주차를 종료할까요? 예약이 끝나요.')) endParking(); }}>{busy ? '처리 중...' : '주차 종료'}</button>}
        </div> : <div className="empty"><Ticket size={36} /><h2>아직 예약 내역이 없어요</h2><p>주차장 지도에서 빈자리를 선택해 보세요.</p><button className="primary" onClick={() => focusZone('A')}>주차장 지도 보기 <ChevronRight size={17} /></button></div>}
      </>}

      {page === 'help' && <>
        <div className="page-head"><span className="eyebrow">GUIDE</span><h1>이용 안내</h1><p>예약 후 입구에서 차량번호를 인식하면 공용 차단봉이 자동으로 열려요.</p></div>
        <div className="guide">{[
          ['01', '주차 공간 예약', '지도에서 빈자리를 골라 숫자 4자리 차량번호로 예약해요. 30분 안에 입차하지 않으면 자동 취소돼요.'],
          ['02', '입구 자동 인식', '차단봉 앞에서 초음파 센서가 차를 감지하면 카메라가 번호판을 확인하고, 예약 차량이면 차단봉이 열려요.'],
          ['03', '주차하기 → 주차 완료', "차를 대기 전에 '주차하기'를 누르고, 다 댄 뒤 '주차 완료'를 눌러요. 자리가 비어 있으면 경고가 떠요."],
          ['04', '주차 종료', "나갈 때 '주차 종료'를 눌러야 예약이 끝나요. 예약된 자리에 '주차하기' 없이 차가 들어오면 경고가 떠요."]
        ].map(([n, t, d]) => <div className="guide-item" key={n}><span>{n}</span><div><strong>{t}</strong><p>{d}</p></div></div>)}</div>
        <div className="settings"><h2>데이터 연결 <small>개발 설정</small></h2><p>데모 모드는 이 브라우저에 저장돼요. Flask API가 준비되면 서버 연결로 전환하세요.</p><div className="mode-buttons"><button className={mode === 'demo' ? 'active' : ''} onClick={() => changeMode('demo')}><WifiOff size={16} /> 데모</button><button className={mode === 'api' ? 'active' : ''} onClick={() => changeMode('api')}><Wifi size={16} /> Flask 서버</button></div></div>
      </>}
    </main>

    <nav className="bottom-nav">
      <button className={page === 'home' ? 'active' : ''} onClick={() => setPage('home')}><House size={21} /><span>홈</span></button>
      <button className={page === 'map' ? 'active' : ''} onClick={() => setPage('map')}><MapPin size={21} /><span>주차장 지도</span></button>
      <button className={page === 'ticket' ? 'active' : ''} onClick={() => setPage('ticket')}><Ticket size={21} /><span>내 예약</span></button>
      <button className={page === 'help' ? 'active' : ''} onClick={() => setPage('help')}><Info size={21} /><span>안내</span></button>
    </nav>

    {notice && <div className="toast" role="status">{notice}<button aria-label="닫기" onClick={() => setNotice('')}><X size={17} /></button></div>}

    {selectedSpace && <div className="overlay" onClick={() => setSelected(null)}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label="주차 예약" onClick={e => e.stopPropagation()}>
        <div className="sheet-handle" />
        <div className="sheet-head"><div><span className="eyebrow">RESERVATION</span><h2>{selected} 주차면 예약</h2></div><button aria-label="닫기" onClick={() => setSelected(null)}><X size={21} /></button></div>
        <div className="selected-spot"><CarFront size={23} /><strong>A구역 · {selected}</strong><span>예약 가능</span></div>
        <label htmlFor="plate">차량번호 (숫자 4자리)</label>
        <input id="plate" autoFocus autoComplete="off" inputMode="numeric" pattern="[0-9]*" placeholder="예: 1234"
          value={plate} onChange={e => setPlate(onlyDigits(e.target.value))} onKeyDown={e => { if (e.key === 'Enter' && plateValid) reserve(); }} />
        <p className="hint">{plate && !plateValid ? '숫자 4자리를 모두 입력해 주세요.' : '차량 지붕에 붙인 번호와 같은 숫자 4자리를 입력하세요.'}</p>
        <button className="primary" disabled={busy || !plateValid} onClick={reserve}>{busy ? '예약 처리 중...' : '예약 완료하기'} <ChevronRight size={18} /></button>
      </div>
    </div>}
  </div>;
}

function MiniMap({ spaces }) {
  return <div className="mini-map"><div className="mini-building b1" /><div className="mini-building b2" /><div className="mini-building b3" /><div className="mini-road" /><div className="mini-parking">{spaces.map(s => <span key={s.id} className={status(s)}>{s.id}</span>)}</div><div className="mini-tag">A ZONE</div></div>;
}

function ParkingMap({ spaces, onZone, onSpot }) {
  const zones = [{ id: 'B', x: 32, y: 40 }, { id: 'C', x: 422, y: 40 }, { id: 'D', x: 32, y: 330 }];
  return <svg className="parking-svg" viewBox="0 0 780 600" role="img" aria-label="제1주차장 전체 지도. A B C D 구역 중 A구역의 A1부터 A5까지 예약할 수 있습니다">
    <defs><pattern id="grid" width="22" height="22" patternUnits="userSpaceOnUse"><path d="M22 0H0V22" fill="none" stroke="#e7eeeb" strokeWidth="1" /></pattern></defs>
    <rect width="780" height="600" fill="#f3f7f4" /><rect width="780" height="600" fill="url(#grid)" />
    <rect x="0" y="267" width="780" height="65" fill="#dce5e2" /><path d="M0 299H780" stroke="white" strokeWidth="3" strokeDasharray="18 14" />
    <rect x="359" y="0" width="65" height="600" fill="#dce5e2" /><path d="M391 0V600" stroke="white" strokeWidth="3" strokeDasharray="18 14" />
    {zones.map(z => <g key={z.id} onClick={() => onZone(z.id)} style={{ cursor: 'pointer' }}><rect x={z.x} y={z.y} width="300" height="205" rx="16" fill="#e9eeec" stroke="#c9d4cf" strokeWidth="2" /><text x={z.x + 20} y={z.y + 33} fontSize="20" fill="#64746d" fontWeight="800">{z.id}구역</text><text x={z.x + 20} y={z.y + 53} fontSize="11" fill="#8c9992">예약 미지원</text>{Array.from({ length: 12 }, (_, i) => { const x = z.x + 18 + (i % 6) * 46, y = z.y + 76 + Math.floor(i / 6) * 56; return <g key={i}><rect x={x} y={y} width="38" height="46" rx="5" fill="#d5ddda" stroke="#b7c3be" /><path d={`M${x + 7} ${y + 10}H${x + 31}`} stroke="#c3ceca" strokeWidth="2" /></g>; })}</g>)}
    <rect x="443" y="349" width="303" height="205" rx="16" fill="#e7f5eb" stroke="#4da773" strokeWidth="3" onClick={() => onZone('A')} style={{ cursor: 'pointer' }} />
    <text x="462" y="383" fontSize="21" fontWeight="800" fill="#187745" onClick={() => onZone('A')} style={{ cursor: 'pointer' }}>A구역</text><text x="462" y="405" fontSize="12" fill="#44825c">실시간 감지 · 예약 운영</text>
    {spaces.map((p, i) => { const x = 453 + i * 57, y = 425, st = status(p), [fill, stroke] = svgColors[st]; return <g key={p.id} onClick={() => onSpot(p.id)} style={{ cursor: 'pointer' }}><rect x={x} y={y} width="51" height="105" rx="8" fill={fill} stroke={stroke} strokeWidth="2.5" /><text x={x + 25.5} y={y + 44} textAnchor="middle" fontSize="17" fontWeight="800" fill="#264334">{p.id}</text><text x={x + 25.5} y={y + 72} textAnchor="middle" fontSize="9" fill="#385a49">{labelOf(p)}</text></g>; })}
    <g onClick={() => onZone('A')} style={{ cursor: 'pointer' }}><rect x="603" y="363" width="124" height="31" rx="15" fill="#177d47" /><text x="665" y="383" textAnchor="middle" fontSize="12" fill="white" fontWeight="700">A구역 확대 +</text></g>
    <rect x="318" y="567" width="144" height="26" rx="13" fill="#fff" stroke="#dce5df" /><text x="390" y="584" textAnchor="middle" fontSize="12" fill="#50665a" fontWeight="700">↑ 제1주차장 입구</text>
  </svg>;
}

createRoot(document.getElementById('root')).render(<App />);
