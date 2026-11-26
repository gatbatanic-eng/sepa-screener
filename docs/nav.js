/* 대시보드 공통 메뉴.
 *
 * 모든 페이지는 메뉴 자리에 아래 두 줄만 둔다(KEY는 아래 PAGES의 key, 홈은 src="nav.js").
 *   <nav id="gc-nav" data-page="KEY"></nav><script src="../nav.js"></script>
 * 탭을 추가·삭제·이름 변경·묶음 이동할 때는 이 파일의 GROUPS만 고친다.
 * 위 줄 = 묶음, 아래 줄 = 고른 묶음의 페이지. 페이지 색상 변수(--accent 등)가 있으면 따르고 없으면 기본색을 쓴다.
 */
(function () {
  "use strict";

  var GROUPS = [
    { key: "today", label: "오늘", pages: [
      { key: "market", label: "오늘의 돈과 관심", path: "market/" },
      { key: "advisory", label: "투자 자문 위원회", path: "advisory/" },
      { key: "picks", label: "오늘의 추천", path: "picks/" },
    ] },
    { key: "screens", label: "스크리너", pages: [
      { key: "sepa", label: "SEPA 추세템플릿", path: "" },
      { key: "technical", label: "기술적 신호", path: "technical/" },
      { key: "screener", label: "멀티팩터", path: "screener/" },
      { key: "range_vrebound", label: "RANGE · V-REBOUND", path: "range_vrebound/" },
      { key: "funnel", label: "실적 턴어라운드", path: "funnel/" },
      { key: "recovery", label: "계좌복구 공격매매", path: "recovery/" },
    ] },
    { key: "results", label: "성과", pages: [
      { key: "performance", label: "전략 현황", path: "performance/" },
      { key: "agents", label: "에이전트 리그", path: "agents/" },
    ] },
    { key: "lab", label: "연구실", pages: [
      { key: "fpd", label: "FPD 연구", path: "fpd/" },
      { key: "accumulation", label: "매집 필터", path: "accumulation/" },
      { key: "disclosures", label: "공시 성장 검증", path: "disclosures/" },
      { key: "entry_lab", label: "조기 진입 연구", path: "entry_lab/" },
    ] },
  ];

  var script = document.currentScript;
  var base = script ? script.src.replace(/[^/]*$/, "") : "./";
  var mount = document.getElementById("gc-nav");
  if (!mount) {
    mount = document.createElement("nav");
    mount.id = "gc-nav";
    if (script && script.parentNode) script.parentNode.insertBefore(mount, script);
    else document.body.insertBefore(mount, document.body.firstChild);
  }

  function findCurrent() {
    var want = mount.getAttribute("data-page");
    var here = location.pathname.replace(/index\.html$/, "");
    for (var g = 0; g < GROUPS.length; g++) {
      for (var p = 0; p < GROUPS[g].pages.length; p++) {
        var page = GROUPS[g].pages[p];
        if (want ? page.key === want : new URL(base + page.path).pathname === here) return { group: GROUPS[g], page: page };
      }
    }
    return { group: GROUPS[1], page: null };
  }

  if (!document.getElementById("gc-nav-style")) {
    var style = document.createElement("style");
    style.id = "gc-nav-style";
    style.textContent = [
      "#gc-nav{display:block;margin:10px 0 14px;font:13px/1.3 system-ui,-apple-system,'Segoe UI',sans-serif}",
      "#gc-nav .gc-row{display:flex;flex-wrap:wrap;gap:6px}",
      "#gc-nav .gc-groups{gap:4px;border-bottom:1px solid color-mix(in srgb,currentColor 18%,transparent);margin-bottom:8px}",
      "#gc-nav .gc-group{appearance:none;background:none;border:0;border-bottom:2px solid transparent;margin-bottom:-1px;padding:6px 10px;",
      "font:inherit;font-weight:600;color:inherit;opacity:.6;cursor:pointer}",
      "#gc-nav .gc-group:hover{opacity:1}",
      "#gc-nav .gc-group[aria-selected=true]{opacity:1;border-bottom-color:var(--accent,#2563eb)}",
      "#gc-nav .gc-page{display:inline-block;padding:5px 10px;border-radius:999px;text-decoration:none;color:inherit;opacity:.8;",
      "border:1px solid color-mix(in srgb,currentColor 22%,transparent);font-size:12px}",
      "#gc-nav .gc-page:hover{opacity:1;border-color:var(--accent,#2563eb)}",
      "#gc-nav .gc-page[aria-current=page]{opacity:1;color:#fff;background:var(--accent,#2563eb);border-color:var(--accent,#2563eb)}",
    ].join("");
    document.head.appendChild(style);
  }

  var current = findCurrent();
  mount.setAttribute("aria-label", "대시보드 메뉴");
  mount.innerHTML = "";
  var groupRow = document.createElement("div");
  groupRow.className = "gc-row gc-groups";
  groupRow.setAttribute("role", "tablist");
  var pageRow = document.createElement("div");
  pageRow.className = "gc-row gc-pages";
  mount.appendChild(groupRow);
  mount.appendChild(pageRow);

  function showGroup(group) {
    var buttons = groupRow.querySelectorAll(".gc-group");
    for (var i = 0; i < buttons.length; i++) buttons[i].setAttribute("aria-selected", String(buttons[i].getAttribute("data-group") === group.key));
    pageRow.innerHTML = "";
    group.pages.forEach(function (page) {
      var a = document.createElement("a");
      a.className = "gc-page";
      a.href = base + page.path;
      a.textContent = page.label;
      if (current.page && page.key === current.page.key) a.setAttribute("aria-current", "page");
      pageRow.appendChild(a);
    });
  }

  GROUPS.forEach(function (group) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "gc-group";
    b.setAttribute("role", "tab");
    b.setAttribute("data-group", group.key);
    b.textContent = group.label;
    b.addEventListener("click", function () { showGroup(group); });
    groupRow.appendChild(b);
  });
  showGroup(current.group);
})();
