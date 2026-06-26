/* ════════════════════════════════════════════════════════════
 * 우리만의 공간 — Alpine.js SPA 메인 로직
 * ════════════════════════════════════════════════════════════ */

/* ── 작은 헬퍼 ─────────────────────────────────────────────── */
async function api(path, opts = {}) {
  const r = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (r.status === 401) { location.href = '/'; throw new Error('unauthenticated'); }
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || `HTTP ${r.status}`);
  }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
}

const isoDay = (d) => d.toISOString().slice(0, 10);
const today = () => isoDay(new Date());

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]
  ));
}

/* 챗봇 출력용 라이트 마크다운 렌더러 (ArQuant 채팅 출력 로직 참조).
   - 항상 esc() 로 먼저 이스케이프 -> 우리가 만든 태그만 주입하므로 XSS 안전.
   - 볼드(별표)/밑줄/인라인코드/헤더/구분선 마크다운 마커는 전부 제거(평문화).
   - 마크다운 표(파이프 행)는 table 로 렌더. */
function mdLite(text) {
  const stripInline = s => esc(s)
    .replace(/\*\*+/g, '').replace(/__+/g, '')
    .replace(/`([^`]+)`/g, '$1').replace(/`/g, '')
    .trim();
  const isRow = l => /^\s*\|.*\|\s*$/.test(l);
  const isSep = l => /^\s*\|[\s\-:|]+\|\s*$/.test(l);
  const lines = String(text ?? '').split('\n');
  let html = '', i = 0, plain = [];
  const flush = () => {
    if (!plain.length) return;
    let t = plain.join('\n')
      .replace(/```[^\n]*/g, '')
      .replace(/^#{1,6}\s*/gm, '')
      .replace(/^\s*---+\s*$/gm, '');
    t = stripInline(t).replace(/\n{3,}/g, '\n\n');
    if (t) html += `<span class="md-p">${t}</span>`;
    plain = [];
  };
  while (i < lines.length) {
    if (isRow(lines[i])) {
      flush();
      const rows = [];
      while (i < lines.length && isRow(lines[i])) { rows.push(lines[i]); i++; }
      let body = false, tbl = '<table class="md-table">';
      for (const r of rows) {
        if (isSep(r)) { body = true; continue; }
        const cells = r.trim().replace(/^\||\|$/g, '').split('|');
        const tag = body ? 'td' : 'th';
        tbl += '<tr>' + cells.map(c => `<${tag}>${stripInline(c)}</${tag}>`).join('') + '</tr>';
      }
      html += tbl + '</table>';
    } else { plain.push(lines[i]); i++; }
  }
  flush();
  return html || '<span class="md-p"></span>';
}

/* ISO 날짜(YYYY-MM-DD)를 하루 증가. 로컬 달력 기준(월말/연말 자동 보정). */
function nextIso(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d + 1);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

function timeago(iso) {
  if (!iso) return '';
  // 서버 timestamp 는 UTC. tz 표기가 없으면 'Z'를 붙여 UTC로 파싱(안 그러면 로컬=KST로 읽혀 9시간 어긋남).
  let str = String(iso).replace(' ', 'T');
  if (!/[zZ]$/.test(str) && !/[+-]\d\d:?\d\d$/.test(str)) str += 'Z';
  const t = new Date(str).getTime();
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 60) return '방금';
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
}

/* ── Alpine 컴포넌트 ───────────────────────────────────────── */
function appState() {
  const boot = window.APP_BOOT || {};
  return {
    /* ── 상태 ────────────────────────────────────────────── */
    me: boot.me || '',
    nicks: { a: boot.nickname_a, b: boot.nickname_b, self: '', partner: '' },
    tab: 'home',
    dday: { days_together: null, since: '', next_milestone_days: 0, next_milestone_in: 0, next_anniversary: '', next_anniversary_in: 0, birthdays: [] },

    photos: [],
    photoQuery: '',
    photoPlace: '',
    photoLoading: false,
    photoPlaces: [],
    photoView: null,
    photoSelect: false,                 // 다중 선택 모드
    photoSelected: [],                  // 선택된 사진 id 목록
    bulkPlaceForm: { open: false, place_name: '' },

    events: [],
    cal: { year: 0, month: 0 },
    calSelected: today(),
    eventForm: { open: false, id: null, title: '', due: today(), end_date: '', time: '', note: '', color: '#ec4899', reminder_minutes: null },
    allEvents: false,                   // 홈 '전체 일정' 모달

    // 오늘 한마디 — 캘린더는 선택일(dayNotes), 홈 위젯은 오늘(homeNote)
    dayNotes: { mine: '', partner: null, loading: false, isAnniv: false },
    homeNote: { mine: '', partner: null },
    notesByDate: {},                    // 캘린더 셀 표시용: { 'YYYY-MM-DD': [{content, mine, ...}] }
    // 온보딩 — 신규 커플 만난날짜·생일·닉네임
    isMemberA: false,
    onboarding: { open: false, anniversary: '', birthday: '', nickname: '', saving: false },

    bucket: [],
    bucketForm: { open: false, id: null, title: '', description: '', icon: '💖', target_date: '' },

    places: [],
    mapFilter: 'all',
    placeForm: { open: false, name: '', address: '', lat: null, lng: null, kind: 'wishlist', category: '', memo: '' },
    placeEdit: { open: false, id: null, name: '', address: '', category: '', memo: '', kind: 'wishlist', lat: null, lng: null },
    mapFull: false,                      // 지도 전체화면 모드
    kakaoLoaded: false,
    kakaoKey: !!boot.kakao_loaded,       // 키 존재 여부(맵 컨테이너 가시화 기준)
    map: null,
    mapMarkers: [],
    mapSearchQuery: '',
    searchResults: [],
    geocoder: null,
    placesService: null,
    _markerImgCache: {},
    kakaoImport: { open:false, url:'', items:[], picked:[], kind:'wishlist', loading:false, msg:'' },   // 카카오맵 폴더 가져오기

    chat: { messages: [], input: '', thinking: false, sessionId: 'default' },
    chatGreeting: '안녕~ 🐰 둘이 오늘 뭐 할지 알려줘봐!',

    pokePresets: [],
    pokePicker: false,
    pokeMessage: '',
    pokes: [],
    unreadPokes: 0,
    pokeAnim: false,

    settings: { anniversary_date: boot.anniversary, nickname_a: boot.nickname_a, nickname_b: boot.nickname_b, theme: boot.theme, mascot: boot.mascot, allowed_emails: [] },
    themes: [
      { id: 'rosy',   label: '로지',  swatch: 'linear-gradient(135deg,#ffe4e8,#ffc7d0)' },
      { id: 'mint',   label: '민트',  swatch: 'linear-gradient(135deg,#d1fae5,#a7f3d0)' },
      { id: 'butter', label: '버터',  swatch: 'linear-gradient(135deg,#fef3c7,#fde68a)' },
      { id: 'lavender', label: '라벤더', swatch: 'linear-gradient(135deg,#ede9fe,#ddd6fe)' },
      { id: 'sky',    label: '하늘',  swatch: 'linear-gradient(135deg,#dbeafe,#bae6fd)' },
    ],
    mascots: [
      { id: 'bunny', label: '토끼',  emoji: '🐰' },
      { id: 'cat',   label: '고양이', emoji: '🐱' },
      { id: 'bear',  label: '곰',    emoji: '🐻' },
    ],

    toasts: [],
    ws: { sock: null, online: false, retries: 0 },

    /* ── 파생값 ──────────────────────────────────────────── */
    get stats() {
      return {
        photos: this.photos.length,
        places: this.places.filter(p => p.kind === 'visited').length,
        saved: this.places.length,               // 저장한 장소 전체(가볼/또갈/가본)
        bucket_done: this.bucket.filter(b => b.done).length,
      };
    },
    get upcomingEvents() {
      const now = today();
      // 다중일 일정은 종료일까지 '진행 중'으로 보아 아직 안 끝났으면 포함.
      return this.events
        .filter(e => !e.done && e.due && (e.end_date || e.due) >= now)
        .sort((a, b) => (a.due + (a.time || '')).localeCompare(b.due + (b.time || '')));
    },
    get filteredPhotos() {
      let arr = this.photos;
      if (this.photoQuery) {
        const q = this.photoQuery.toLowerCase();
        arr = arr.filter(p => (p.caption || '').toLowerCase().includes(q)
          || (p.place_name || '').toLowerCase().includes(q));
      }
      if (this.photoPlace) arr = arr.filter(p => p.place_name === this.photoPlace);
      return arr;
    },
    get filteredPlaces() {
      if (this.mapFilter === 'all') return this.places;
      return this.places.filter(p => p.kind === this.mapFilter);
    },
    get recentPokes() { return this.pokes.slice(0, 8); },
    get mascotEmoji() {
      const m = this.mascots.find(x => x.id === this.settings.mascot);
      return m ? m.emoji : '🐰';
    },
    get chatSuggestions() {
      const h = new Date().getHours();
      if (h < 11) return ['오늘 뭐 먹지?', '근처 브런치 추천'];
      if (h < 15) return ['점심 뭐 먹지?', '카페 추천해줘'];
      if (h < 18) return ['오후 데이트 코스', '예쁜 카페'];
      if (h < 22) return ['저녁 데이트 코스', '야경 좋은 곳'];
      return ['야식 추천', '내일 뭐 할까?'];
    },
    get calCells() {
      const y = this.cal.year, m = this.cal.month;
      const first = new Date(y, m, 1);
      const startDow = first.getDay();
      const daysInMonth = new Date(y, m + 1, 0).getDate();
      const prevDays = new Date(y, m, 0).getDate();
      const cells = [];
      const todayIso = today();
      const byDate = {};
      for (const e of this.events) {
        if (!e.due) continue;
        // 다중일 일정은 시작~종료 사이 모든 날짜에 표시(점). guard 로 폭주 방지.
        const end = (e.end_date && e.end_date > e.due) ? e.end_date : e.due;
        let cur = e.due;
        for (let guard = 0; cur <= end && guard < 366; guard++) {
          (byDate[cur] = byDate[cur] || []).push(e);
          cur = nextIso(cur);
        }
      }
      for (let i = 0; i < 42; i++) {
        const dayNum = i - startDow + 1;
        let inMonth = true, day = dayNum, year = y, month = m;
        if (dayNum < 1) { day = prevDays + dayNum; month -= 1; inMonth = false; }
        else if (dayNum > daysInMonth) { day = dayNum - daysInMonth; month += 1; inMonth = false; }
        if (month < 0) { month = 11; year -= 1; }
        if (month > 11) { month = 0; year += 1; }
        const iso = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        cells.push({
          key: iso, iso, day, inMonth,
          isToday: iso === todayIso,
          events: byDate[iso] || [],
          notes: this.notesByDate[iso] || [],   // 그 날 기록된 오늘 한마디
        });
      }
      return cells;
    },
    get dayEvents() {
      const sel = this.calSelected;
      // 선택일이 [시작, 종료] 범위 안이면 포함(ISO 날짜는 문자열 비교=날짜 비교).
      return this.events.filter(e => {
        if (!e.due) return false;
        const end = e.end_date || e.due;
        return e.due <= sel && sel <= end;
      });
    },

    /* ── 부팅 ────────────────────────────────────────────── */
    async init() {
      const now = new Date();
      this.cal.year = now.getFullYear();
      this.cal.month = now.getMonth();
      this.applyTheme(this.settings.theme);
      await Promise.all([
        this.loadMe(),
        this.loadDDay(),
        this.refreshPhotos(),
        this.refreshEvents(),
        this.refreshNotes(),
        this.refreshBucket(),
        this.refreshPlaces(),
        this.loadPokePresets(),
        this.refreshPokes(),
        this.loadSettings(),
      ]);
      this.connectWS();
      this.greetChat();
      this.loadHomeNote();          // 홈 오늘 한마디
      this.checkOnboarding();       // 신규 커플이면 만난날짜·생일 입력 유도
      // 알림 권한
      if ('Notification' in window && Notification.permission === 'default') {
        try { Notification.requestPermission(); } catch (_) {}
      }
    },

    /* ── 사용자 ──────────────────────────────────────────── */
    async loadMe() {
      const j = await api('/api/auth/me');
      if (!j.authenticated) { location.href = '/'; return; }
      this.me = j.email;
      this.isMemberA = !!j.is_member_a;
      this.nicks.self = j.nickname_self || this.nicks.a;
      this.nicks.partner = j.nickname_partner || this.nicks.b;
      // 온보딩: 서버가 raw kv(미설정=None)로 판정. 폼은 이미 가진 값으로 프리필.
      this._needsOnboarding = !!j.needs_onboarding;
      this.onboarding.anniversary = j.anniversary_raw || '';
      this.onboarding.birthday = j.my_birthday_raw || '';
      this.onboarding.nickname = j.nickname_self || '';
    },
    async logout() {
      // 앱 세션 쿠키 삭제 후 로그인 화면으로. (CF Access 의존 제거)
      try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (_) {}
      location.href = '/';
    },
    async unlinkCouple() {
      const r = await fetch('/api/couple/unlink', { method: 'POST' });
      if (r.ok) location.href = '/';
    },
    async kakaoPreview(){
      this.kakaoImport.loading=true; this.kakaoImport.msg='';
      try{
        const r = await fetch('/api/places/import/kakao',{method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({url:this.kakaoImport.url})});
        const j = await r.json();
        if(!r.ok){ this.kakaoImport.msg = j.detail==='no_places_parsed'?'장소를 못 읽었어요 (공유 링크 확인)':'가져오기 실패'; return; }
        this.kakaoImport.items = j.items;
        this.kakaoImport.picked = j.items.map((_,i)=>i);
      } finally { this.kakaoImport.loading=false; }
    },
    async kakaoConfirm(){
      const sel = this.kakaoImport.picked.map(i=>this.kakaoImport.items[i]);
      const r = await fetch('/api/places/import/kakao/confirm',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({items:sel, kind:this.kakaoImport.kind})});
      const j = await r.json();
      this.kakaoImport.msg = `${j.added}곳 추가됐어요 📍`;
      this.kakaoImport.open=false;
      if(this.refreshPlaces) this.refreshPlaces();
    },
    /* ── D-day ──────────────────────────────────────────── */
    async loadDDay() {
      this.dday = await api('/api/settings/dday');
    },

    /* ── 사진 ────────────────────────────────────────────── */
    async refreshPhotos() {
      this.photoLoading = true;
      try {
        this.photos = await api('/api/photos');
        this.photoPlaces = await api('/api/photos/places');
      } finally { this.photoLoading = false; }
    },
    async uploadPhotos(files) {
      const arr = Array.from(files);
      this.pushToast('📤', `${arr.length}장 업로드 중…`);
      for (const file of arr) {
        const fd = new FormData();
        fd.append('file', file);
        try {
          const r = await fetch('/api/photos/upload', { method: 'POST', body: fd });
          if (!r.ok) throw new Error('업로드 실패');
        } catch (e) {
          this.pushToast('⚠️', '업로드 실패', e.message);
        }
      }
      await this.refreshPhotos();
      this.pushToast('💖', `${arr.length}장 추가됐어!`);
    },
    openPhoto(p) { this.photoView = { ...p }; },
    async savePhotoMeta() {
      if (!this.photoView) return;
      const p = this.photoView;
      await api(`/api/photos/${p.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ caption: p.caption, place_name: p.place_name }),
      });
      // 로컬 리스트 갱신
      const idx = this.photos.findIndex(x => x.id === p.id);
      if (idx >= 0) this.photos[idx] = { ...this.photos[idx], caption: p.caption, place_name: p.place_name };
    },
    async deletePhoto(p) {
      if (!confirm('이 사진을 지울까요?')) return;
      await api(`/api/photos/${p.id}`, { method: 'DELETE' });
      this.photos = this.photos.filter(x => x.id !== p.id);
      this.photoView = null;
    },
    downloadPhoto(p) {
      const a = document.createElement('a');
      a.href = p.url; a.download = (p.caption || p.id) + '.jpg';
      document.body.appendChild(a); a.click(); a.remove();
    },
    /* 다중 선택 */
    togglePhotoSelect() {
      this.photoSelect = !this.photoSelect;
      this.photoSelected = [];
    },
    isPhotoSelected(p) { return this.photoSelected.includes(p.id); },
    togglePhotoSel(p) {
      const i = this.photoSelected.indexOf(p.id);
      if (i >= 0) this.photoSelected.splice(i, 1);
      else this.photoSelected.push(p.id);
    },
    selectAllPhotos() { this.photoSelected = this.filteredPhotos.map(p => p.id); },
    clearPhotoSel() { this.photoSelected = []; },
    async bulkDeletePhotos() {
      if (!this.photoSelected.length) return;
      if (!confirm(`선택한 ${this.photoSelected.length}장을 지울까요?`)) return;
      const n = this.photoSelected.length;
      await api('/api/photos/bulk_delete', { method: 'POST', body: JSON.stringify({ ids: this.photoSelected }) });
      const set = new Set(this.photoSelected);
      this.photos = this.photos.filter(p => !set.has(p.id));
      this.photoSelected = []; this.photoSelect = false;
      this.pushToast('🗑️', `${n}장 삭제됐어`);
      this.refreshPhotos();
    },
    openBulkPlace() {
      if (!this.photoSelected.length) { this.pushToast('📍', '사진을 먼저 선택해줘'); return; }
      this.bulkPlaceForm = { open: true, place_name: '' };
    },
    async submitBulkPlace() {
      const name = (this.bulkPlaceForm.place_name || '').trim();
      const n = this.photoSelected.length;
      await api('/api/photos/bulk_place', { method: 'POST', body: JSON.stringify({ ids: this.photoSelected, place_name: name }) });
      const set = new Set(this.photoSelected);
      this.photos = this.photos.map(p => set.has(p.id) ? { ...p, place_name: name } : p);
      this.bulkPlaceForm.open = false;
      this.photoSelected = []; this.photoSelect = false;
      this.pushToast('📍', `${n}장 장소 변경: ${name || '(지움)'}`);
      this.refreshPhotos();
    },

    /* ── 캘린더/이벤트 ────────────────────────────────────── */
    async refreshEvents() {
      this.events = await api('/api/events');
    },
    // 캘린더 셀에 날짜별 오늘 한마디를 표시하기 위해 커플 전체 한마디를 한 번에 적재.
    async refreshNotes() {
      const rows = await api('/api/notes/all');
      const map = {};
      for (const r of rows) (map[r.date] = map[r.date] || []).push(r);
      this.notesByDate = map;
    },
    calPrev() {
      this.cal.month -= 1;
      if (this.cal.month < 0) { this.cal.month = 11; this.cal.year -= 1; }
    },
    calNext() {
      this.cal.month += 1;
      if (this.cal.month > 11) { this.cal.month = 0; this.cal.year += 1; }
    },
    selectDay(cell) {
      this.calSelected = cell.iso;
      if (!cell.inMonth) {
        const [y, m] = cell.iso.split('-').map(Number);
        this.cal.year = y; this.cal.month = m - 1;
      }
      this.loadDayNote();
    },

    /* ── 오늘 한마디 ─────────────────────────────────────── */
    async _fetchNote(date) {
      const j = await api('/api/notes?date=' + encodeURIComponent(date));
      return { mine: j.mine ? j.mine.content : '', partner: j.partner || null };
    },
    _isAnniversary(date) {
      const a = this.settings.anniversary_date;       // YYYY-MM-DD
      return !!(a && date && a.slice(5) === date.slice(5));   // 월·일 일치
    },
    async loadDayNote() {
      this.dayNotes.loading = true;
      try {
        const n = await this._fetchNote(this.calSelected);
        this.dayNotes.mine = n.mine;
        this.dayNotes.partner = n.partner;
        this.dayNotes.isAnniv = this._isAnniversary(this.calSelected);
      } finally { this.dayNotes.loading = false; }
    },
    async saveDayNote() {
      await api('/api/notes', { method: 'PUT',
        body: JSON.stringify({ date: this.calSelected, content: this.dayNotes.mine }) });
      await this.loadDayNote();
      this.refreshNotes();
      if (this.calSelected === today()) Object.assign(this.homeNote, await this._fetchNote(today()));
      this.pushToast('✏️', '오늘 한마디 저장');
    },
    async loadHomeNote() {
      Object.assign(this.homeNote, await this._fetchNote(today()));
    },
    async saveHomeNote() {
      await api('/api/notes', { method: 'PUT',
        body: JSON.stringify({ date: today(), content: this.homeNote.mine }) });
      await this.loadHomeNote();
      this.refreshNotes();
      if (this.calSelected === today()) await this.loadDayNote();
      this.pushToast('✏️', '오늘 한마디 저장');
    },

    /* ── 온보딩 (만난날짜·생일·닉네임) ───────────────────── */
    _myBirthdayKey() { return this.isMemberA ? 'birthday_a' : 'birthday_b'; },
    _myNickKey() { return this.isMemberA ? 'nickname_a' : 'nickname_b'; },
    checkOnboarding() {
      // 판정은 서버(loadMe 의 needs_onboarding). 폼은 loadMe 가 raw 값으로 프리필해 둠.
      if (this._needsOnboarding) this.onboarding.open = true;
    },
    async saveOnboarding() {
      this.onboarding.saving = true;
      try {
        const payload = {};
        if (this.onboarding.anniversary) payload.anniversary_date = this.onboarding.anniversary;
        if (this.onboarding.nickname) payload[this._myNickKey()] = this.onboarding.nickname;
        if (this.onboarding.birthday) payload[this._myBirthdayKey()] = this.onboarding.birthday;
        await api('/api/settings', { method: 'PATCH', body: JSON.stringify(payload) });
        await this.loadSettings();
        await this.loadDDay();
        this.onboarding.open = false;
        this.pushToast('💞', '우리 정보 저장됐어');
      } finally { this.onboarding.saving = false; }
    },
    openEventForm(due) {
      this.eventForm = { open: true, id: null, title: '', due: due || today(), end_date: '', time: '', note: '', color: '#ec4899', reminder_minutes: null };
    },
    editEvent(e) {
      this.eventForm = {
        open: true, id: e.id, title: e.title || '', due: e.due || today(),
        end_date: e.end_date || '',
        time: e.time || '', note: e.note || '', color: e.color || '#ec4899',
        reminder_minutes: e.reminder_minutes ?? null,
      };
    },
    async submitEvent() {
      const f = this.eventForm;
      const payload = {
        title: f.title, due: f.due || null,
        // 종료일은 항상 보냄: 날짜=다중일, ''=지움(하루). (PATCH 에서 ''→NULL 처리)
        end_date: f.end_date || '',
        time: f.time || null,
        note: f.note || null, color: f.color,
        reminder_minutes: f.reminder_minutes ? Number(f.reminder_minutes) : null,
      };
      if (f.id) {
        await api(`/api/events/${f.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
      } else {
        await api('/api/events', { method: 'POST', body: JSON.stringify({ ...payload, source: 'calendar' }) });
      }
      f.open = false;
      await this.refreshEvents();
      this.pushToast('📅', f.id ? '일정이 수정됐어!' : '일정이 추가됐어!');
    },
    async toggleEvent(e) {
      e.done = !e.done;
      await api(`/api/events/${e.id}`, { method: 'PATCH', body: JSON.stringify({ done: e.done }) });
    },
    async deleteEvent(e) {
      if (!confirm('일정을 삭제할까요?')) return;
      await api(`/api/events/${e.id}`, { method: 'DELETE' });
      this.events = this.events.filter(x => x.id !== e.id);
    },
    formatEvent(e) {
      const parts = [];
      if (e.due) parts.push(e.due === today() ? '오늘' : e.due);
      // 다중일 일정이면 종료일을 함께(같은 연도면 MM-DD 만 짧게).
      if (e.end_date && e.end_date > e.due) {
        const end = e.end_date.slice(0, 4) === (e.due || '').slice(0, 4) ? e.end_date.slice(5) : e.end_date;
        parts.push('~ ' + end);
      }
      if (e.time) parts.push(e.time);
      return parts.join(' ');
    },
    formatDate(iso) {
      if (!iso) return '';
      try { return iso.slice(0, 10).replace(/-/g, '.'); } catch { return iso; }
    },

    /* ── 버킷리스트 ──────────────────────────────────────── */
    async refreshBucket() { this.bucket = await api('/api/bucket'); },
    openBucketForm() {
      this.bucketForm = { open: true, id: null, title: '', description: '', icon: '💖', target_date: '' };
    },
    editBucket(b) {
      this.bucketForm = {
        open: true, id: b.id, title: b.title || '', description: b.description || '',
        icon: b.icon || '💖', target_date: b.target_date || '',
      };
    },
    async submitBucket() {
      const f = this.bucketForm;
      const payload = {
        title: f.title, description: f.description || null, icon: f.icon,
        target_date: f.target_date || null,
      };
      if (f.id) {
        await api(`/api/bucket/${f.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
      } else {
        await api('/api/bucket', { method: 'POST', body: JSON.stringify(payload) });
      }
      f.open = false;
      await this.refreshBucket();
      this.pushToast('✨', f.id ? '버킷이 수정됐어!' : '버킷에 담았어!');
    },
    async toggleBucket(b) {
      b.done = !b.done;
      await api(`/api/bucket/${b.id}`, { method: 'PATCH', body: JSON.stringify({ done: b.done }) });
      if (b.done) this.pushToast('💖', '둘이 하나 더 이뤘다!', b.title);
    },
    async deleteBucket(b) {
      if (!confirm('이 버킷을 지울까요?')) return;
      await api(`/api/bucket/${b.id}`, { method: 'DELETE' });
      this.bucket = this.bucket.filter(x => x.id !== b.id);
    },

    /* ── 지도 + 장소 ─────────────────────────────────────── */
    async refreshPlaces() { this.places = await api('/api/places'); this.refreshMapMarkers(); },
    openPlaceForm() {
      // 좌표는 사용자에게 노출하지 않는다. 검색 선택/지도 클릭으로 채워지거나,
      // 주소만 입력하면 submit 시 지오코딩으로 좌표를 구한다.
      this.placeForm = { open: true, name: '', address: '', lat: null, lng: null, kind: 'wishlist', category: '', memo: '' };
    },
    // 장소 추가 폼에서 '현재 위치'를 좌표로 채운다(주소는 역지오코딩으로 자동).
    // 이름/메모는 사용자가 적고, 기존 submitPlace 가 그대로 저장한다.
    useCurrentLocation() {
      if (!navigator.geolocation) { this.pushToast('⚠️', 'geolocation 미지원'); return; }
      this.pushToast('📡', '현재 위치 찾는 중…');
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const { latitude: lat, longitude: lng, accuracy } = pos.coords;
          this.placeForm.lat = lat;
          this.placeForm.lng = lng;
          this.reverseGeocode(lat, lng);          // 주소가 비어있으면 자동 채움
          const m = Math.round(accuracy || 0);
          const acc = m > 1000 ? `±${(m / 1000).toFixed(1)}km` : `±${m}m`;
          this.pushToast('📍', `현재 위치 적용 (${acc})`, '이름과 메모를 적고 저장해줘');
        },
        (e) => this.pushToast('⚠️', '위치 거부됨', e.message),
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
      );
    },
    async submitPlace() {
      const f = this.placeForm;
      if (f.lat == null || f.lng == null) {
        const ok = await this.geocodePlace(f.address || f.name);
        if (!ok) { this.pushToast('📍', '주소를 못 찾았어', '검색으로 고르거나 주소를 더 정확히 적어줘'); return; }
      }
      await api('/api/places', { method: 'POST', body: JSON.stringify(f) });
      f.open = false;
      await this.refreshPlaces();
      this.pushToast('📍', '장소가 추가됐어!');
    },
    // 주소 → 좌표(카카오 지오코더). 성공 시 placeForm.lat/lng 채우고 true.
    geocodePlace(query) {
      return new Promise((resolve) => {
        const q = (query || '').trim();
        if (!q || !this.geocoder) return resolve(false);
        this.geocoder.addressSearch(q, (res, status) => {
          if (status === kakao.maps.services.Status.OK && res[0]) {
            this.placeForm.lat = parseFloat(res[0].y);
            this.placeForm.lng = parseFloat(res[0].x);
            resolve(true);
          } else resolve(false);
        });
      });
    },
    async deletePlace(p) {
      if (!confirm('이 장소를 지울까요?')) return;
      await api(`/api/places/${p.id}`, { method: 'DELETE' });
      this.places = this.places.filter(x => x.id !== p.id);
      this.refreshMapMarkers();
    },
    panTo(p) {
      this.tab = 'map';
      this.mountMap();
      setTimeout(() => {
        if (this.map) this.map.setCenter(new kakao.maps.LatLng(p.lat, p.lng));
      }, 200);
    },
    mountMap() {
      if (!window.kakao || !kakao.maps) {
        // SDK 스크립트가 안 떴다(보통 Kakao 앱키에 이 도메인이 미등록 → SDK 차단).
        if (this.kakaoKey && !this._kakaoWarned) {
          this._kakaoWarned = true;
          this.pushToast('🗺️', '지도 SDK 로드 실패', 'Kakao 앱키에 couple.ai-ve.uk 도메인 등록이 필요해');
        }
        return;
      }
      // 이미 만든 맵이면, 탭 전환으로 막 보이게 됐을 수 있으니 레이아웃을 다시 잡는다.
      if (this.map) { setTimeout(() => this.map.relayout(), 80); return; }
      kakao.maps.load(() => {
        const container = document.getElementById('kakao-map');
        if (!container) return;
        this.map = new kakao.maps.Map(container, {
          center: new kakao.maps.LatLng(37.5665, 126.9780),
          level: 6,
        });
        // 컨트롤: 일반/스카이뷰 + 줌
        this.map.addControl(new kakao.maps.MapTypeControl(),
          kakao.maps.ControlPosition.TOPRIGHT);
        this.map.addControl(new kakao.maps.ZoomControl(),
          kakao.maps.ControlPosition.RIGHT);
        // 서비스
        this.placesService = new kakao.maps.services.Places();
        this.geocoder = new kakao.maps.services.Geocoder();
        // 우클릭(데스크탑) / 롱프레스(모바일은 SDK가 자동 매핑) → 폼 + 역지오코딩
        kakao.maps.event.addListener(this.map, 'rightclick', (e) => {
          const ll = e.latLng;
          this.placeForm = {
            open: true, name: '', address: '',
            lat: ll.getLat(), lng: ll.getLng(),
            kind: 'wishlist', category: '', memo: '',
          };
          this.reverseGeocode(ll.getLat(), ll.getLng());
        });
        this.kakaoLoaded = true;
        this.refreshMapMarkers();
        // 컨테이너가 display:none → 막 보이게 된 직후일 수 있어, 크기를 다시 계산해야
        // 회색/빈 화면으로 안 깨진다. (숨김 상태에서 생성되면 0x0 으로 렌더되는 버그)
        setTimeout(() => { if (this.map) this.map.relayout(); }, 120);
        // 지도를 처음 열면 내 위치로 이동(조용히). 권한 거부/실패 시 기본 서울 중심 유지.
        if (!this._initiallyLocated) {
          this._initiallyLocated = true;
          setTimeout(() => this._locate(true), 200);
        }
      });
    },
    _markerImage(kind) {
      if (!window.kakao) return null;
      if (this._markerImgCache[kind]) return this._markerImgCache[kind];
      // 종류별 핀 색: 가볼곳(보라)·가본곳(로즈)·또갈곳(앰버)
      const palette = {
        wishlist: ['#a78bfa', '#7c3aed'],
        visited:  ['#fb7185', '#db2777'],
        revisit:  ['#fbbf24', '#f59e0b'],
      };
      const [c1, c2] = palette[kind] || palette.wishlist;
      // 따옴표/꺾쇠 인코딩 문제로 깨지지 않게 encodeURIComponent 사용(투명 네모 버그 수정).
      const svg =
        `<svg xmlns="http://www.w3.org/2000/svg" width="38" height="46" viewBox="0 0 38 46">` +
        `<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">` +
        `<stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/>` +
        `</linearGradient></defs>` +
        `<path d="M19 1C9 1 1 9 1 19c0 10 18 26 18 26s18-16 18-26C37 9 29 1 19 1z" ` +
        `fill="url(#g)" stroke="#ffffff" stroke-width="2"/>` +
        `<circle cx="19" cy="18" r="6.5" fill="#ffffff"/>` +
        `</svg>`;
      const url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
      const size = new kakao.maps.Size(38, 46);
      const img = new kakao.maps.MarkerImage(url, size, { offset: new kakao.maps.Point(19, 46) });
      this._markerImgCache[kind] = img;
      return img;
    },
    /* 장소 종류 표시 헬퍼 */
    kindEmoji(k) { return k === 'visited' ? '💗' : (k === 'revisit' ? '🔁' : '⭐'); },
    kindLabel(k) { return k === 'visited' ? '가본 곳' : (k === 'revisit' ? '또갈 곳' : '가볼 곳'); },
    // 드롭다운에서 가볼곳/또갈곳/가본곳을 직접 선택 → PATCH
    async setKind(p, kind) {
      if (!kind || kind === p.kind) return;
      const prev = p.kind;
      p.kind = kind;
      try {
        await api(`/api/places/${p.id}`, { method: 'PATCH', body: JSON.stringify({ kind }) });
        this.refreshMapMarkers();
        this.pushToast(this.kindEmoji(kind), `${p.name} → ${this.kindLabel(kind)}`);
      } catch (e) {
        p.kind = prev;            // 실패 시 원복
        this.pushToast('⚠️', '종류 변경 실패', e.message);
      }
    },
    // 카카오맵 길찾기(외부 앱/브라우저로)
    directionsTo(p) {
      const url = `https://map.kakao.com/link/to/${encodeURIComponent(p.name)},${p.lat},${p.lng}`;
      window.open(url, '_blank');
    },
    refreshMapMarkers() {
      if (!this.map || !kakao?.maps) return;
      for (const m of this.mapMarkers) m.setMap(null);
      this.mapMarkers = [];
      for (const p of this.filteredPlaces) {
        const pos = new kakao.maps.LatLng(p.lat, p.lng);
        const marker = new kakao.maps.Marker({
          position: pos, map: this.map, title: p.name,
          image: this._markerImage(p.kind),
          draggable: true,
        });
        // 드래그로 위치 미세 조정 → 서버에 PATCH
        kakao.maps.event.addListener(marker, 'dragend', async () => {
          const ll = marker.getPosition();
          p.lat = ll.getLat(); p.lng = ll.getLng();
          try {
            await api(`/api/places/${p.id}`, {
              method: 'PATCH', body: JSON.stringify({ lat: p.lat, lng: p.lng }),
            });
            this.pushToast('📍', '위치 업데이트', p.name);
          } catch (e) { this.pushToast('⚠️', '실패', e.message); }
        });
        const iw = new kakao.maps.InfoWindow({
          content: `<div style="padding:6px 10px;font-size:12px;font-family:Pretendard">
            ${this.kindEmoji(p.kind)} <b>${esc(p.name)}</b>
            ${p.memo ? `<br><span style="color:#888">${esc(p.memo)}</span>` : ''}
          </div>`,
        });
        kakao.maps.event.addListener(marker, 'click', () => iw.open(this.map, marker));
        this.mapMarkers.push(marker);
      }
    },
    reverseGeocode(lat, lng) {
      if (!this.geocoder) return;
      this.geocoder.coord2Address(lng, lat, (res, status) => {
        if (status === kakao.maps.services.Status.OK && res[0]) {
          const addr = res[0].road_address?.address_name
                    || res[0].address?.address_name || '';
          if (addr && !this.placeForm.address) this.placeForm.address = addr;
        }
      });
    },
    myLocation() {
      if (!navigator.geolocation) { this.pushToast('⚠️','geolocation 미지원'); return; }
      this.tab = 'map'; this.mountMap();
      this.pushToast('📡', '위치 찾는 중…');
      setTimeout(() => this._locate(false), 120);
    },
    // 실제 위치 표시 코어 — 버튼(quiet=false)과 지도 첫 진입(quiet=true)이 공유.
    _locate(quiet) {
      if (!navigator.geolocation || !this.map) return;
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const { latitude: lat, longitude: lng, accuracy } = pos.coords;
          if (!this.map) return;
          const ll = new kakao.maps.LatLng(lat, lng);
          // 이전 내위치 표식 제거
          if (this._myLocMarker) this._myLocMarker.setMap(null);
          if (this._myLocCircle) this._myLocCircle.setMap(null);
          // 오차 반경 원 — 위치가 얼마나 불확실한지 시각화
          this._myLocCircle = new kakao.maps.Circle({
            center: ll, radius: Math.max(accuracy || 0, 20),
            strokeWeight: 1, strokeColor: '#2563eb', strokeOpacity: 0.6,
            fillColor: '#3b82f6', fillOpacity: 0.12, map: this.map,
          });
          // 드래그해서 직접 보정 가능한 마커
          this._myLocMarker = new kakao.maps.Marker({
            position: ll, map: this.map, draggable: true, title: '내 위치 (드래그해서 보정)',
          });
          kakao.maps.event.addListener(this._myLocMarker, 'dragend', () => {
            if (this._myLocCircle) this._myLocCircle.setPosition(this._myLocMarker.getPosition());
          });
          // 정확도가 높을수록 더 확대
          const acc = accuracy || 9999;
          this.map.setCenter(ll);
          this.map.setLevel(acc < 80 ? 3 : acc < 500 ? 5 : 7);
          if (quiet) return;          // 첫 진입은 조용히 — 토스트 없음
          const m = Math.round(accuracy || 0);
          if (m > 1000) {
            this.pushToast('📍', `대략 위치 (±${(m/1000).toFixed(1)}km)`,
              'PC는 GPS가 없어 WiFi/IP로 잡혀 부정확해. 폰 앱에선 정확해 — 마커를 끌어 보정해줘');
          } else {
            this.pushToast('📍', `내 위치 (±${m}m)`, '필요하면 파란 마커를 끌어서 보정해');
          }
        },
        (e) => { if (!quiet) this.pushToast('⚠️', '위치 거부됨', e.message); },
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
      );
    },
    // 지도 전체화면 토글 — 클래스만 바꾸고 카카오에 레이아웃 재계산을 알린다.
    toggleMapFullscreen() {
      this.mapFull = !this.mapFull;
      this.$nextTick(() => { if (this.map) this.map.relayout(); });
      setTimeout(() => { if (this.map) this.map.relayout(); }, 250);
    },
    // 장소 상세 — 목록 항목 클릭 시 열람/편집.
    openPlaceDetail(p) {
      this.placeEdit = {
        open: true, id: p.id, name: p.name || '', address: p.address || '',
        category: p.category || '', memo: p.memo || '', kind: p.kind || 'wishlist',
        lat: p.lat, lng: p.lng,
      };
    },
    async savePlaceDetail() {
      const f = this.placeEdit;
      if (!f.name.trim()) { this.pushToast('⚠️', '이름을 적어줘'); return; }
      try {
        await api(`/api/places/${f.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ name: f.name, address: f.address, category: f.category, memo: f.memo, kind: f.kind }),
        });
        const i = this.places.findIndex(x => x.id === f.id);
        if (i >= 0) this.places[i] = { ...this.places[i], name: f.name, address: f.address, category: f.category, memo: f.memo, kind: f.kind };
        f.open = false;
        this.refreshMapMarkers();
        this.pushToast('💾', '장소 정보 저장됨', f.name);
      } catch (e) { this.pushToast('⚠️', '저장 실패', e.message); }
    },
    fitAllPlaces() {
      if (!this.map || !this.filteredPlaces.length) {
        this.pushToast('📍', '표시할 핀이 없어');
        return;
      }
      const bounds = new kakao.maps.LatLngBounds();
      for (const p of this.filteredPlaces) {
        bounds.extend(new kakao.maps.LatLng(p.lat, p.lng));
      }
      this.map.setBounds(bounds);
    },
    searchPlaces() {
      const q = (this.mapSearchQuery || '').trim();
      if (!q) { this.searchResults = []; return; }
      if (!this.placesService) { this.pushToast('⚠️','지도 먼저 로드'); return; }
      this.placesService.keywordSearch(q, (data, status) => {
        if (status === kakao.maps.services.Status.OK) {
          this.searchResults = data;
          if (this.map && data.length) {
            const b = new kakao.maps.LatLngBounds();
            for (const r of data) b.extend(new kakao.maps.LatLng(+r.y, +r.x));
            this.map.setBounds(b);
          }
        } else {
          this.searchResults = [];
          this.pushToast('🔎', '검색 결과 없음', `"${q}"`);
        }
      });
    },
    pickSearchResult(r) {
      this.placeForm = {
        open: true,
        name: r.place_name,
        address: r.road_address_name || r.address_name || '',
        lat: parseFloat(r.y), lng: parseFloat(r.x),
        kind: 'wishlist',
        category: (r.category_name || '').split(' > ').pop() || '',
        memo: '',
      };
      this.searchResults = [];
      this.mapSearchQuery = '';
      this.map?.setCenter(new kakao.maps.LatLng(+r.y, +r.x));
    },

    /* ── 챗봇 ────────────────────────────────────────────── */
    async greetChat() {
      try {
        const j = await api('/api/chat/greeting');
        this.chatGreeting = j.text;
        this.chat.messages = await api(`/api/chat/history?session_id=${this.chat.sessionId}`)
          .then(arr => arr.map((m, i) => ({ ...m, id: 'h' + i })));
      } catch (_) {}
    },
    // 인사말만 다시 받아온다(대화 내역은 건드리지 않음).
    // 마스코트를 바꾸면 서버가 인사말에 구워 넣는 이모지(🐰/🐱/🐻)도 따라 바뀐다.
    async refreshGreeting() {
      try { this.chatGreeting = (await api('/api/chat/greeting')).text; } catch (_) {}
    },
    async sendChat() {
      const msg = (this.chat.input || '').trim();
      if (!msg) return;
      this.chat.input = '';
      this.chat.messages.push({ id: 'u' + Date.now(), role: 'user', content: msg, user_email: this.me });
      this.chat.thinking = true;
      this.$nextTick(() => this.scrollChat());
      try {
        const j = await api('/api/chat/send', {
          method: 'POST',
          body: JSON.stringify({ message: msg, session_id: this.chat.sessionId }),
        });
        this.chat.messages.push({
          id: 'b' + Date.now(), role: 'assistant',
          content: j.reply, tools: j.tools_used || [],
        });
        // 도구가 데이터를 바꿨으면 본인 화면도 갱신
        if ((j.tools_used || []).length) {
          this.refreshAll();
        }
      } catch (e) {
        this.chat.messages.push({ id: 'er' + Date.now(), role: 'assistant', content: `🥹 ${e.message}` });
      } finally {
        this.chat.thinking = false;
        this.$nextTick(() => this.scrollChat());
      }
    },
    refreshAll() {
      Promise.all([
        this.refreshPlaces(), this.refreshEvents(), this.refreshBucket(),
        this.refreshPhotos(), this.loadSettings(), this.loadDDay(),
      ]).catch(()=>{});
    },
    scrollChat() {
      const box = this.$refs.chatBox;
      if (box) box.scrollTop = box.scrollHeight;
    },
    async clearChat() {
      if (!confirm('대화 내역을 지울까요?')) return;
      await api(`/api/chat/history?session_id=${this.chat.sessionId}`, { method: 'DELETE' });
      this.chat.messages = [];
      this.greetChat();
    },
    /* 메시지 발신자 라벨/스타일 — 현호·숙영·코코 구분해 표시 */
    nickFor(email) {
      const em = this.settings.allowed_emails || [];
      if (email && em[0] && email === em[0]) return this.settings.nickname_a || this.nicks.a;
      if (email && em[1] && email === em[1]) return this.settings.nickname_b || this.nicks.b;
      return email === this.me ? (this.nicks.self || '나') : (this.nicks.partner || '상대');
    },
    senderLabel(m) {
      return m.role === 'assistant' ? ('코코 ' + this.mascotEmoji) : this.nickFor(m.user_email);
    },
    bubbleClass(m) {
      if (m.role === 'assistant') return 'bot';
      return (m.user_email && m.user_email !== this.me) ? 'peer' : 'user';
    },

    /* ── 콕찌르기 ────────────────────────────────────────── */
    async loadPokePresets() { this.pokePresets = await api('/api/pokes/presets'); },
    async refreshPokes() {
      this.pokes = await api('/api/pokes');
      const u = await api('/api/pokes/unread_count');
      this.unreadPokes = u.unread;
    },
    async sendPoke(emoji, message) {
      try {
        await api('/api/pokes', { method: 'POST', body: JSON.stringify({ emoji, message: message || null }) });
        this.pokePicker = false;
        this.pokeMessage = '';
        this.pokeAnim = true; setTimeout(() => this.pokeAnim = false, 600);
        this.pushToast('💗', '콕! 보냈어');
        await this.refreshPokes();
      } catch (e) { this.pushToast('⚠️', '실패', e.message); }
    },
    async markPokesSeen() {
      await api('/api/pokes/seen', { method: 'POST' });
      this.unreadPokes = 0;
    },
    async clearPokes() {
      if (!confirm('최근 콕찌르기 기록을 모두 지울까요?')) return;
      await api('/api/pokes/clear', { method: 'POST' });
      this.pokes = [];
      this.unreadPokes = 0;
      this.pushToast('🧹', '콕 기록을 비웠어');
    },

    /* ── 설정 ────────────────────────────────────────────── */
    async loadSettings() {
      const s = await api('/api/settings');
      this.settings = { ...this.settings, ...s };
      this.nicks.a = s.nickname_a; this.nicks.b = s.nickname_b;
      this.applyTheme(s.theme);
    },
    async saveSettings() {
      const s = await api('/api/settings', {
        method: 'PATCH', body: JSON.stringify(this.settings),
      });
      this.settings = { ...this.settings, ...s };
      this.nicks.a = s.nickname_a; this.nicks.b = s.nickname_b;
      this.applyTheme(s.theme);
      await this.loadDDay();
      this.refreshGreeting();          // 마스코트가 바뀌면 인사말 이모지도 갱신
      this.pushToast('💾', '저장됐어');
    },
    openSettings() { this.tab = 'settings'; },
    applyTheme(id) {
      // data-theme 속성만 바꾼다 → CSS 변수(--rXXX)가 전부 따라 바뀌어
      // 배경·버튼·상자·폰트·칩 색이 일괄 전환된다. (CSS 의 body[data-theme=...] 참고)
      const valid = ['rosy', 'mint', 'butter', 'lavender', 'sky'];
      document.body.dataset.theme = valid.includes(id) ? id : 'rosy';
    },

    /* ── 토스트 ──────────────────────────────────────────── */
    pushToast(icon, title, body) {
      const id = Date.now() + Math.random();
      this.toasts.push({ id, icon, title, body });
      setTimeout(() => this.dismissToast(id), 4200);
    },
    dismissToast(id) { this.toasts = this.toasts.filter(t => t.id !== id); },

    /* ── 시스템 알림 ──────────────────────────────────────────
       안드로이드 앱(WebView) 안이면 네이티브 브리지로 시스템 알림을 띄우고,
       일반 브라우저면 Web Notification 으로 폴백한다(점진적 향상). */
    notifyNative(title, body, tag) {
      try {
        if (window.CoupleNative && typeof window.CoupleNative.notify === 'function') {
          window.CoupleNative.notify(JSON.stringify({ title, body: body || '', tag: tag || '' }));
          return;
        }
      } catch (_) {}
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(title, { body: body || '' });
      }
    },

    /* ── WebSocket ───────────────────────────────────────── */
    connectWS() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const sock = new WebSocket(`${proto}://${location.host}/ws`);
      this.ws.sock = sock;
      sock.onopen = () => { this.ws.online = true; this.ws.retries = 0; };
      sock.onclose = () => {
        this.ws.online = false;
        const delay = Math.min(15000, 1000 * (2 ** Math.min(this.ws.retries, 4)));
        this.ws.retries++;
        setTimeout(() => this.connectWS(), delay);
      };
      sock.onmessage = (e) => {
        let data; try { data = JSON.parse(e.data); } catch { return; }
        this.handleWS(data);
      };
      // keepalive
      setInterval(() => { try { sock.readyState === 1 && sock.send('ping'); } catch (_) {} }, 25000);
    },
    handleWS(d) {
      if (d.kind === 'poke') {
        this.pushToast(d.emoji, `${this.nicks.partner}님 콕!`, d.message);
        this.unreadPokes++;
        this.pokeAnim = true; setTimeout(() => this.pokeAnim = false, 600);
        this.refreshPokes();
        this.notifyNative(`${this.nicks.partner}님 콕! ${d.emoji}`, d.message || '', 'poke');
        if (navigator.vibrate) navigator.vibrate([60, 30, 60]);
      } else if (d.kind === 'reminder') {
        this.pushToast('⏰', `리마인더: ${d.title}`, `${d.due} ${d.time}`);
        this.notifyNative(`⏰ ${d.title}`, `${d.due} ${d.time}`, 'reminder');
      } else if (d.kind === 'event_added') {
        this.pushToast('📅', `${this.nicks.partner}님 일정 추가`, d.title);
        this.refreshEvents();
      } else if (d.kind === 'event_done') {
        this.refreshEvents();
      } else if (d.kind === 'bucket_added') {
        this.pushToast('✨', `${this.nicks.partner}님 버킷 추가`, d.title);
        this.refreshBucket();
      } else if (d.kind === 'bucket_done') {
        this.pushToast('💖', `${this.nicks.partner}님 버킷 완료!`);
        this.refreshBucket();
      } else if (d.kind === 'place_added') {
        this.pushToast('📍', `${this.nicks.partner}님 장소 추가`, d.name);
        this.refreshPlaces();
      } else if (d.kind === 'place_updated') {
        this.refreshPlaces();
      } else if (d.kind === 'place_deleted') {
        this.pushToast('🗑️', `장소 삭제됨`, d.name || '');
        this.refreshPlaces();
      } else if (d.kind === 'note') {
        this.pushToast('✏️', `${this.nicks.partner}님 오늘 한마디`);
        this.refreshNotes();
        if (d.date === today()) this.loadHomeNote();
        if (d.date === this.calSelected) this.loadDayNote();
      }
    },

    /* 헬퍼 노출 */
    timeago, today, mdLite,
  };
}
