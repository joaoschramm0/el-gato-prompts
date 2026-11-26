/* El Gato — app.js — puro, sem build, funciona em file://
   Tenta /api/score se backend presente, senão TF-IDF local + corpus mock de 5 prompts
*/
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var input = $("promptInput");
  var btnMeasure = $("btnMeasure");
  var btnClear = $("btnClear");
  var scoreNum = $("scoreNum");
  var scoreLabel = $("scoreLabel");
  var scoreSub = $("scoreSub");
  var top3El = $("top3");
  var shareBody = $("shareBody");
  var modeBadge = $("modeBadge");
  var footerMode = $("footerMode");
  var gaugeFill = $("gaugeFill");
  var needle = $("needle");
  var btnCopy = $("btnCopy");
  var btnCopyPrompt = $("btnCopyPrompt");

  // --- corpus mock: 5 prompts (1 do print + 4 genéricos) ---
  var CORPUS = [
    { id: 1, text: "aprovo a árvore, e o jogo cadê?", source: "print — footer Opus 5.5" },
    { id: 2, text: "crie um jogo completo com placar e sprites, não só a estrutura", source: "mock #2" },
    { id: 3, text: "gere o projeto jogável agora, sem placeholders", source: "mock #3" },
    { id: 4, text: "aprovo o design, mas quero ver o gameplay funcionando", source: "mock #4" },
    { id: 5, text: "implemente o loop principal e controles antes de pedir aprovação", source: "mock #5" }
  ];

  // tokenização simples (pt-BR friendly)
  function tokenize(s) {
    return (s || "").toLowerCase()
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim().split(/\s+/).filter(Boolean);
  }

  // TF-IDF pré-computado sobre o corpus
  var vocab = {};
  var docTF = []; // array of {token: count}
  var df = {};
  var idf = {};
  var docVec = []; // array of {token: tfidf}
  var docNorm = [];

  (function buildIndex() {
    var N = CORPUS.length;
    for (var i = 0; i < N; i++) {
      var toks = tokenize(CORPUS[i].text);
      var tf = {};
      var seen = {};
      for (var k = 0; k < toks.length; k++) {
        var t = toks[k];
        vocab[t] = 1;
        tf[t] = (tf[t] || 0) + 1;
        if (!seen[t]) { df[t] = (df[t] || 0) + 1; seen[t] = 1; }
      }
      // normaliza TF por tamanho do doc
      var len = toks.length || 1;
      for (var tt in tf) tf[tt] = tf[tt] / len;
      docTF.push(tf);
    }
    for (var term in df) {
      idf[term] = Math.log((N + 1) / (df[term] + 1)) + 1; // smoothed
    }
    for (var d = 0; d < N; d++) {
      var vec = {};
      var sum2 = 0;
      for (var term2 in docTF[d]) {
        var w = docTF[d][term2] * (idf[term2] || Math.log(N + 1));
        vec[term2] = w;
        sum2 += w * w;
      }
      docVec.push(vec);
      docNorm.push(Math.sqrt(sum2) || 1);
    }
  })();

  function vectorForQuery(q) {
    var toks = tokenize(q);
    if (!toks.length) return { vec: {}, norm: 1, toks: [] };
    var tf = {};
    for (var i = 0; i < toks.length; i++) tf[toks[i]] = (tf[toks[i]] || 0) + 1;
    var len = toks.length;
    for (var t in tf) tf[t] /= len;
    var vec = {};
    var sum2 = 0;
    var N = CORPUS.length;
    for (var term in tf) {
      var w = tf[term] * (idf[term] || Math.log(N + 1));
      vec[term] = w;
      sum2 += w * w;
    }
    return { vec: vec, norm: Math.sqrt(sum2) || 1, toks: toks };
  }

  function cosine(aVec, aNorm, bVec, bNorm) {
    var dot = 0;
    // itera no menor
    var small = aVec, big = bVec;
    // count keys
    var aKeys = 0, bKeys = 0;
    for (var k in aVec) aKeys++;
    for (var k2 in bVec) bKeys++;
    if (aKeys > bKeys) { small = bVec; big = aVec; }
    for (var term in small) {
      if (big[term]) dot += small[term] * big[term];
    }
    if (aNorm === 0 || bNorm === 0) return 0;
    return dot / (aNorm * bNorm);
  }

  // PRNG determinístico leve baseado no texto (para jitter estável, não aleatório a cada clique)
  function hash01(str) {
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 1000) / 1000; // 0..0.999
  }

  function scoreLocal(query) {
    var q = vectorForQuery(query);
    var sims = [];
    for (var i = 0; i < CORPUS.length; i++) {
      var c = cosine(q.vec, q.norm, docVec[i], docNorm[i]);
      // overlap lexical bonus (token intersection / union-like)
      var qSet = {};
      for (var qi = 0; qi < q.toks.length; qi++) qSet[q.toks[qi]] = 1;
      var cToks = tokenize(CORPUS[i].text);
      var inter = 0;
      for (var ci = 0; ci < cToks.length; ci++) if (qSet[cToks[ci]]) inter++;
      var overlap = q.toks.length ? inter / Math.max(q.toks.length, cToks.length) : 0;
      var blended = 0.72 * c + 0.28 * overlap;
      // jitter determinístico ±0.04 (levemente random pra demo, mas estável por input)
      var jitter = (hash01(query + "|" + i) - 0.5) * 0.08;
      var finalSim = Math.max(0, Math.min(1, blended + jitter));
      // pequeno boost se query muito curta e vazia, mantém baixo
      sims.push({ idx: i, sim: finalSim, cosine: c, overlap: overlap });
    }
    sims.sort(function (a, b) { return b.sim - a.sim; });
    var top = sims[0] ? sims[0].sim : 0;
    // mapeia para 0-100: curva levemente agressiva pra demo ficar interessante
    // 0.0 -> 12, 0.35 -> 58, 0.65 -> 84, 1.0 -> 98
    var score = Math.round(12 + 86 * Math.pow(top, 0.62));
    // se query vazia, força 0
    if (!query.trim()) score = 0;
    score = Math.max(0, Math.min(100, score));
    var ranked = sims.slice(0, 3).map(function (s) {
      return { text: CORPUS[s.idx].text, source: CORPUS[s.idx].source, score: Math.round(s.sim * 100), sim: s.sim };
    });
    return { score: score, top: ranked, sims: sims };
  }

  function labelFor(score) {
    if (score === 0) return "SEM SINAL";
    if (score < 30) return "FRIO — BAIXA SIMILARIDADE";
    if (score < 60) return "MORNO — SIMILARIDADE MÉDIA";
    if (score < 82) return "QUENTE — ALTA SIMILARIDADE";
    return "EL GATO! — MUITO PRÓXIMO";
  }

  function colorFor(score) {
    if (score < 30) return "#ef4444";
    if (score < 60) return "#f59e0b";
    return "#22c55e";
  }

  function renderGauge(score) {
    // gaugeFill: pathLength 100, dashoffset 100 -> 0 (0% vazio, 100% cheio)
    var pct = Math.max(0, Math.min(100, score));
    gaugeFill.style.strokeDashoffset = String(100 - pct);
    // needle: -90deg (0%) -> +90deg (100%)
    var deg = -90 + (pct / 100) * 180;
    needle.style.transform = "rotate(" + deg + "deg)";
    scoreNum.textContent = pct + "%";
    scoreNum.style.color = colorFor(pct);
    scoreLabel.textContent = labelFor(pct);
    scoreLabel.style.color = colorFor(pct);
  }

  function renderTop3(items) {
    top3El.innerHTML = "";
    if (!items.length) {
      top3El.innerHTML = '<div style="font-size:13px;color:var(--muted)">Nenhum resultado ainda.</div>';
      return;
    }
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var div = document.createElement("div");
      div.className = "top-item";
      var rank = document.createElement("div");
      rank.className = "top-rank";
      rank.textContent = String(i + 1);
      var txt = document.createElement("div");
      txt.className = "top-text";
      txt.textContent = '"' + it.text + '"';
      var sub = document.createElement("div");
      sub.style.cssText = "display:flex;flex-direction:column;align-items:flex-end;gap:2px";
      var sc = document.createElement("div");
      sc.className = "top-score";
      sc.textContent = it.score + "%";
      sc.style.color = colorFor(it.score);
      var src = document.createElement("div");
      src.style.cssText = "font-size:11px;color:#71717a";
      src.textContent = it.source;
      sub.appendChild(sc); sub.appendChild(src);
      div.appendChild(rank); div.appendChild(txt); div.appendChild(sub);
      top3El.appendChild(div);
    }
  }

  function renderShare(score, query, top) {
    var excerpt = (query || "").trim().slice(0, 140);
    if (excerpt.length < (query || "").trim().length) excerpt += "…";
    var closest = top && top[0] ? '"' + top[0].text + '"' : "—";
    var label = labelFor(score);
    var txt = "🐱 El Gato — " + score + "% — " + label + "\n"
      + "Prompt: " + (excerpt || "(vazio)") + "\n"
      + "Mais próximo: " + closest + "\n"
      + "el-gato.local";
    shareBody.textContent = txt;
    return txt;
  }

  var lastQuery = "";
  var lastResult = null;
  var usingBackend = false;

  async function tryBackend(query) {
    // Só tenta se não for file:// e se /api/score responder rápido
    if (location.protocol === "file:") return null;
    try {
      var ctrl = new AbortController();
      var t = setTimeout(function () { ctrl.abort(); }, 1200);
      var res = await fetch("/api/score", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: query }),
        signal: ctrl.signal
      });
      clearTimeout(t);
      if (!res.ok) return null;
      var data = await res.json();
      // espera {score:0-100, closest:[{text,score}]}
      if (typeof data.score !== "number") return null;
      return data;
    } catch (e) { return null; }
  }

  async function measure() {
    var q = input.value || "";
    lastQuery = q;
    if (!q.trim()) {
      renderGauge(0);
      scoreSub.textContent = "Digite um prompt para medir.";
      renderTop3([]);
      renderShare(0, q, []);
      return;
    }
    btnMeasure.disabled = true;
    btnMeasure.textContent = "◉ Medindo…";
    scoreSub.textContent = "Calculando similaridade…";

    var backend = await tryBackend(q);
    var result;
    if (backend) {
      usingBackend = true;
      modeBadge.textContent = "backend /api/score";
      footerMode.textContent = "via backend";
      var score = Math.max(0, Math.min(100, Math.round(backend.score)));
      var top = (backend.closest || backend.top || []).slice(0, 3).map(function (c, i) {
        return { text: c.text || c.prompt || String(c), source: c.source || ("corpus #" + (i + 1)), score: Math.round(c.score != null ? c.score : c.sim * 100) };
      });
      // fallback se backend não mandar closest
      if (!top.length) {
        var local = scoreLocal(q);
        top = local.top;
      }
      result = { score: score, top: top };
    } else {
      usingBackend = false;
      modeBadge.textContent = "modo local";
      footerMode.textContent = "TF-IDF local";
      var local2 = scoreLocal(q);
      result = { score: local2.score, top: local2.top };
    }

    lastResult = result;
    renderGauge(result.score);
    renderTop3(result.top);
    renderShare(result.score, q, result.top);
    scoreSub.textContent = usingBackend ? "Pontuado via backend /api/score." : "Pontuado localmente (TF-IDF + overlap).";
    btnMeasure.disabled = false;
    btnMeasure.textContent = "◉ Medir El Gato";
  }

  // init: placeholder state
  renderGauge(0);
  renderTop3([]);
  shareBody.textContent = "Seu resultado aparecerá aqui. Meça um prompt para gerar o card.";

  btnMeasure.addEventListener("click", measure);
  btnClear.addEventListener("click", function () {
    input.value = "";
    input.focus();
    renderGauge(0);
    scoreSub.textContent = "Cole um prompt e clique em Medir El Gato.";
    renderTop3([]);
    renderShare(0, "", []);
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); measure(); }
  });
  btnCopy.addEventListener("click", async function () {
    var txt = shareBody.textContent || "";
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(txt);
      else { var ta = document.createElement("textarea"); ta.value = txt; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
      var prev = btnCopy.textContent; btnCopy.textContent = "Copiado!"; setTimeout(function(){ btnCopy.textContent = prev; }, 1200);
    } catch (e) { alert("Copie manualmente:\n\n" + txt); }
  });
  btnCopyPrompt.addEventListener("click", async function () {
    var txt = (input.value || "").trim() || shareBody.textContent || "";
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(txt);
      else { var ta2 = document.createElement("textarea"); ta2.value = txt; document.body.appendChild(ta2); ta2.select(); document.execCommand("copy"); ta2.remove(); }
      var prev2 = btnCopyPrompt.textContent; btnCopyPrompt.textContent = "Copiado!"; setTimeout(function(){ btnCopyPrompt.textContent = prev2; }, 1200);
    } catch (e) { alert(txt); }
  });

  // expõe para debug no console
  window.ElGato = { scoreLocal: scoreLocal, CORPUS: CORPUS };
})();
