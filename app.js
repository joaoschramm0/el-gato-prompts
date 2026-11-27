/* El Gato — medidor local e respostas simuladas, sem build e sem rede.
   A similaridade usa apenas window.ElGatoCorpus, carregado por corpus.js. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var input = $("promptInput");
  var btnMeasure = $("btnMeasure");
  var btnClear = $("btnClear");
  var btnCopy = $("btnCopy");
  var btnCopyPrompt = $("btnCopyPrompt");
  var charCount = $("charCount");
  var corpusStatus = $("corpusStatus");
  var corpusCount = $("corpusCount");
  var footerMode = $("footerMode");
  var meterPanel = $("meterPanel");
  var thermometer = $("thermometer");
  var gaugeBars = Array.prototype.slice.call($("gaugeBars").children);
  var scoreNum = $("scoreNum");
  var scoreLabel = $("scoreLabel");
  var scoreSub = $("scoreSub");
  var claudeReply = $("claudeReply");
  var claudeMood = $("claudeMood");
  var copyStatus = $("copyStatus");
  var btnSound = $("btnSound");
  var craudAudio = $("craudAudio");
  var shell = document.querySelector("main.shell");
  var breakTimer;
  var soundOn = true;
  try {
    if (window.localStorage && window.localStorage.getItem("elgato-sound") === "off") soundOn = false;
  } catch (error) { /* file:// sem storage: segue ligado */ }

  var CORPUS = Array.isArray(window.ElGatoCorpus) ? window.ElGatoCorpus : [];
  var lastReply = "";
  var lastQuery = "";

  function tokenize(text) {
    return (text || "").toLowerCase()
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim().split(/\s+/).filter(Boolean);
  }

  // TF-IDF simples + sobreposição lexical. Não há aleatoriedade nem rede.
  var idf = {};
  var docVec = [];
  var docNorm = [];
  var docTokens = [];

  (function buildIndex() {
    var docTF = [];
    var df = {};
    var total = CORPUS.length;

    for (var i = 0; i < total; i++) {
      var tokens = tokenize(CORPUS[i].text);
      var tf = {};
      var seen = {};
      docTokens.push(tokens);

      for (var j = 0; j < tokens.length; j++) {
        var token = tokens[j];
        tf[token] = (tf[token] || 0) + 1;
        if (!seen[token]) {
          df[token] = (df[token] || 0) + 1;
          seen[token] = true;
        }
      }

      var length = tokens.length || 1;
      for (var term in tf) tf[term] /= length;
      docTF.push(tf);
    }

    for (var term2 in df) idf[term2] = Math.log((total + 1) / (df[term2] + 1)) + 1;

    for (var d = 0; d < total; d++) {
      var vec = {};
      var sum = 0;
      for (var term3 in docTF[d]) {
        var weight = docTF[d][term3] * idf[term3];
        vec[term3] = weight;
        sum += weight * weight;
      }
      docVec.push(vec);
      docNorm.push(Math.sqrt(sum) || 1);
    }
  })();

  function queryVector(query) {
    var tokens = tokenize(query);
    var tf = {};
    var vec = {};
    var sum = 0;

    for (var i = 0; i < tokens.length; i++) tf[tokens[i]] = (tf[tokens[i]] || 0) + 1;
    for (var term in tf) {
      var weight = (tf[term] / (tokens.length || 1)) * (idf[term] || Math.log(CORPUS.length + 1));
      vec[term] = weight;
      sum += weight * weight;
    }

    return { tokens: tokens, vec: vec, norm: Math.sqrt(sum) || 1 };
  }

  function cosine(query, index) {
    var dot = 0;
    var doc = docVec[index];
    for (var term in query.vec) {
      if (doc[term]) dot += query.vec[term] * doc[term];
    }
    return dot / (query.norm * docNorm[index]);
  }

  function scoreLocal(query) {
    if (!(query || "").trim() || !CORPUS.length) return { score: 0, similarity: 0 };

    var q = queryVector(query);
    var unique = {};
    var best = 0;
    for (var i = 0; i < q.tokens.length; i++) unique[q.tokens[i]] = true;

    for (var d = 0; d < CORPUS.length; d++) {
      var intersection = 0;
      for (var j = 0; j < docTokens[d].length; j++) {
        if (unique[docTokens[d][j]]) intersection++;
      }
      var overlap = intersection / Math.max(q.tokens.length, docTokens[d].length, 1);
      var similarity = Math.max(0, Math.min(1, 0.72 * cosine(q, d) + 0.28 * overlap));
      if (similarity > best) best = similarity;
    }

    return {
      score: Math.max(0, Math.min(100, Math.round(100 * Math.pow(best, 0.62)))),
      similarity: best
    };
  }

  function bandFor(score) {
    if (score < 30) return "cold";
    if (score < 60) return "warm";
    if (score < 82) return "hot";
    return "gato";
  }

  function labelFor(score) {
    var band = bandFor(score);
    if (band === "cold") return "FRIO // PROMPT DE REUNIÃO";
    if (band === "warm") return "MORNO // PRIMO DO EL GATO";
    if (band === "hot") return "QUENTE // CHEIRO DE LIVE";
    return "EL GATO // O PATRÃO VOLTOU";
  }

  function colorFor(score) {
    var band = bandFor(score);
    if (band === "cold") return "#ef806d";
    if (band === "warm") return "#e8bb62";
    if (band === "hot") return "#f2a65a";
    return "#b8d94f";
  }

  function subFor(score) {
    var band = bandFor(score);
    if (band === "cold") return "Isso veio de outra live. O chat pediu até pra trocar de aba.";
    if (band === "warm") return "Tem caos, mas ainda pede licença. O El Gato não pede licença.";
    if (band === "hot") return "O chat já está digitando ‘manda print’. Tá quase lá.";
    return "Pode encerrar a live: o patrão voltou e o jogo lagou.";
  }

  function moodFor(score) {
    var band = bandFor(score);
    if (band === "cold") return "paciência: entediado";
    if (band === "warm") return "paciência: desconfiado";
    if (band === "hot") return "paciência: 12%";
    return "paciência: foi de Viper";
  }

  var REPLIES = {
    cold: [
      "Mano, “{prompt}” tá educado demais. Isso é prompt de reunião com pauta. Cadê o desespero, cadê o print?",
      "Cala a boca com essa formalidade. O El Gato já teria cobrado o Viper, a rua neon e mais três prints.",
      "Li “{prompt}” e quase respondi ‘ótima pergunta’. Que fase. Volta quando tiver um jogo quebrado pra cobrar.",
      "Tá frio, hein? Esse prompt tá tão tranquilo que até o Claude quis colaborar. Aqui a gente aprova a árvore e pergunta do jogo.",
      "Nem o lag reconheceu isso. Manda um ‘mostra aí’ e para de escrever como se tivesse todo o tempo do mundo."
    ],
    warm: [
      "“{prompt}” até engana, mano, mas faltou a pressa de quem esperou quatro horas por um carro.",
      "Tô sentindo um El Gato com Wi-Fi ruim: a intenção chegou, o caos ficou no buffer. Essa merda vai lagar.",
      "Você digitou isso com calma, né? Se percebe. Aprovo o começo; agora cadê o jogável?",
      "Já pode entrar na live, mas fica na arquibancada. Manda essa bucetona funcionar antes de pedir licença."
    ],
    hot: [
      "Aí sim, mano. “{prompt}” tem cheiro de live às duas da manhã. Se isso lagar, a culpa é do Viper.",
      "Quase te confundi com o patrão. Só faltou mandar essa bucetona funcionar sem pedir por favor.",
      "Essa eu aprovo. Se travar, não vem chorar: manda print e cobra o jogo.",
      "Chegou cobrando e nem esperou o loading. El Gato vibes. Vai lagar tudo, mas com personalidade."
    ],
    gato: [
      "OOOO CRAUD! “{prompt}” entrou tão na identidade que o jogo já lagou de medo.",
      "EL GATO, É VOCÊ? “{prompt}” passou no teste do caos com louvor.",
      "Aprovo, mano. Essa bucetona tá pronta pra live: cobrança, caos e zero paciência. Pode entrar.",
      "Cala a boca e pega o crachá. Esse prompt é El Gato puro; se eu demorar dois segundos você já manda outro.",
      "Parabéns, essa bucetona de prompt derrubou meu contexto. Vai lagar tudo, mas pelo menos vai ser jogável.",
      "Sem laudo, sem teste A/B, sem reunião: só “{prompt}” e cobrança. O gato está entre nós."
    ]
  };

  function hashString(text) {
    var hash = 2166136261;
    for (var i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function promptExcerpt(query) {
    var clean = (query || "").trim().replace(/\s+/g, " ");
    return clean.length > 100 ? clean.slice(0, 99) + "…" : clean;
  }

  // A escolha também é determinística: o mesmo prompt recebe a mesma zoeira.
  function replyFor(score, query) {
    var band = bandFor(score);
    var options = REPLIES[band];
    var index = hashString((query || "").trim() + "|" + band) % options.length;
    return options[index].split("{prompt}").join(promptExcerpt(query));
  }

  function renderGauge(score, measured) {
    var pct = Math.max(0, Math.min(100, Math.round(score)));
    meterPanel.style.setProperty("--score-color", measured ? colorFor(pct) : "#756f63");
    scoreNum.textContent = measured ? String(pct) : "--";
    scoreLabel.textContent = measured ? labelFor(pct) : "AGUARDANDO MEDIÇÃO";
    scoreSub.textContent = measured ? subFor(pct) : "Nenhum prompt analisado. A paciência ainda está inteira.";
    thermometer.setAttribute("aria-valuenow", String(pct));
    thermometer.setAttribute("aria-valuetext", measured ? pct + "% — " + labelFor(pct) : "Ainda não medido");

    var lit = measured ? Math.round((pct / 100) * gaugeBars.length) : 0;
    for (var i = 0; i < gaugeBars.length; i++) {
      gaugeBars[i].classList.toggle("is-on", i < lit);
      gaugeBars[i].classList.toggle("is-current", lit > 0 && i === lit - 1);
    }
  }

  function setReply(text, placeholder, mood) {
    lastReply = placeholder ? "" : text;
    claudeReply.textContent = text;
    claudeReply.classList.toggle("is-placeholder", Boolean(placeholder));
    claudeMood.textContent = mood;
    btnCopy.disabled = Boolean(placeholder);
  }

  function updateCharCount() {
    var count = input.value.length;
    charCount.textContent = count + (count === 1 ? " caractere" : " caracteres");
  }

  function startScreenEffect(className, duration) {
    if (!shell) return;
    clearTimeout(breakTimer);
    shell.classList.remove("is-breaking");
    shell.classList.remove("is-meltdown");
    void shell.offsetWidth;
    shell.classList.add(className);
    breakTimer = setTimeout(function () {
      shell.classList.remove(className);
    }, duration);
  }

  function breakScreen() {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    startScreenEffect("is-breaking", 1200);
  }

  function meltdownScreen() {
    // O efeito dura até o fim do áudio (~10s a 1.12x ≈ 9s); sem áudio, 9s.
    var ms = 9000;
    if (soundOn && craudAudio && craudAudio.duration && isFinite(craudAudio.duration)) {
      ms = Math.max(3000, (craudAudio.duration / 1.12) * 1000 + 400);
    }
    startScreenEffect("is-meltdown", ms);
    if (craudAudio) {
      craudAudio.onended = function () {
        clearTimeout(breakTimer);
        if (shell) shell.classList.remove("is-meltdown");
      };
    }
  }

  function measure() {
    var query = (input.value || "").trim();
    lastQuery = query;
    copyStatus.textContent = "";

    if (!query) {
      renderGauge(0, true);
      scoreLabel.textContent = "CADÊ O PROMPT?";
      scoreSub.textContent = "O Claude faz muita coisa, mas adivinhar ainda não.";
      setReply("Mano, escreve alguma coisa antes. Até o Viper invisível tinha mais conteúdo que esse campo.", true, "paciência: esperando o prompt");
      input.setAttribute("aria-invalid", "true");
      input.focus();
      return;
    }

    input.setAttribute("aria-invalid", "false");
    if (!CORPUS.length) {
      renderGauge(0, true);
      scoreLabel.textContent = "CORPUS NÃO CARREGOU";
      scoreSub.textContent = "Confira se corpus.js está ao lado deste arquivo.";
      setReply("Sem os prompts reais eu não consigo comparar. Até essa zoeira precisa de contexto.", true, "paciência: corpus offline");
      return;
    }

    var result = scoreLocal(query);
    renderGauge(result.score, true);
    setReply(replyFor(result.score, query), false, moodFor(result.score));
    btnCopyPrompt.disabled = false;
    if (bandFor(result.score) === "gato") {
      var isMeltdown = result.score === 100;
      if (isMeltdown) meltdownScreen();
      else breakScreen();
      if (soundOn && craudAudio) {
        try {
          craudAudio.playbackRate = isMeltdown ? 1.12 : 1;
          craudAudio.currentTime = 0;
          var played = craudAudio.play();
          if (played && played.catch) played.catch(function () { /* áudio bloqueado: segue sem som */ });
        } catch (error) { /* sem áudio: segue sem som */ }
      }
    }
  }

  function fallbackCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.select();
    area.setSelectionRange(0, area.value.length);
    var copied = document.execCommand("copy");
    area.remove();
    return copied;
  }

  async function copyText(text, button, description) {
    if (!text) return;
    var copied = false;

    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        copied = true;
      } catch (error) {
        copied = false;
      }
    }
    if (!copied) {
      try { copied = fallbackCopy(text); } catch (error) { copied = false; }
    }

    if (!copied) {
      copyStatus.textContent = "Não foi possível copiar automaticamente. Selecione o texto e copie manualmente.";
      return;
    }

    clearTimeout(button.copyTimer);
    if (!button.originalLabel) button.originalLabel = button.textContent;
    button.textContent = "Copiado.";
    copyStatus.textContent = description + " copiado para a área de transferência.";
    button.copyTimer = setTimeout(function () {
      button.textContent = button.originalLabel;
    }, 1400);
  }

  btnMeasure.addEventListener("click", measure);
  btnClear.addEventListener("click", function () {
    input.value = "";
    lastQuery = "";
    input.setAttribute("aria-invalid", "false");
    updateCharCount();
    renderGauge(0, false);
    setReply("Manda um prompt aí. Prometo ler antes de fingir que o problema é seu.", true, "paciência: estável (por enquanto)");
    btnCopyPrompt.disabled = true;
    copyStatus.textContent = "";
    input.focus();
  });
  input.addEventListener("input", function () {
    input.setAttribute("aria-invalid", "false");
    btnCopyPrompt.disabled = !input.value.trim();
    updateCharCount();
  });
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      measure();
    }
  });
  btnCopy.addEventListener("click", function () {
    copyText(lastReply, btnCopy, "Resposta");
  });
  function syncSoundButton() {
    btnSound.textContent = soundOn ? "Som: ligado" : "Som: desligado";
    btnSound.setAttribute("aria-pressed", soundOn ? "true" : "false");
  }
  btnSound.addEventListener("click", function () {
    soundOn = !soundOn;
    try {
      if (craudAudio) { craudAudio.pause(); }
      if (window.localStorage) window.localStorage.setItem("elgato-sound", soundOn ? "on" : "off");
    } catch (error) { /* ignora */ }
    if (!soundOn && shell) {
      clearTimeout(breakTimer);
      shell.classList.remove("is-breaking");
      shell.classList.remove("is-meltdown");
    }
    syncSoundButton();
  });
  syncSoundButton();
  btnCopyPrompt.addEventListener("click", function () {
    copyText((input.value || "").trim() || lastQuery, btnCopyPrompt, "Prompt");
  });

  updateCharCount();
  renderGauge(0, false);
  corpusStatus.textContent = CORPUS.length + (CORPUS.length === 1 ? " prompt na memória" : " prompts na memória");
  corpusCount.textContent = CORPUS.length + (CORPUS.length === 1 ? " prompt real" : " prompts reais");
  footerMode.textContent = "TF-IDF LOCAL // " + CORPUS.length + " PROMPTS // FILE:// OK";

  // Útil para testar no console sem expor qualquer lista de correspondências.
  window.ElGato = { scoreLocal: scoreLocal, replyFor: replyFor, CORPUS: CORPUS };
})();
