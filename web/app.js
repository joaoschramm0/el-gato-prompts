/* El Gato — medidor de agressividade e pedidos mal formulados, sem build e sem rede. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var input = $("promptInput");
  var btnMeasure = $("btnMeasure");
  var btnClear = $("btnClear");
  var btnCopy = $("btnCopy");
  var btnCopyPrompt = $("btnCopyPrompt");
  var charCount = $("charCount");
  var aggressionNum = $("aggressionNum");
  var confusionNum = $("confusionNum");
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
  var scene = $("collapseScene");
  var overlay = $("meltdownOverlay");
  var signalMessage = $("signalMessage");
  var screenEffect = null;
  var effectFrame = 0;
  var CLIP_FALLBACK_SECONDS = 10;
  var soundOn = true;
  try {
    if (window.localStorage && window.localStorage.getItem("elgato-sound") === "off") soundOn = false;
  } catch (error) { /* file:// sem storage: segue ligado */ }

  var lastReply = "";
  var lastQuery = "";

  function normalize(text) {
    return text.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/([rs])\1{2,}/g, "$1$1").replace(/([a-qt-z])\1{2,}/g, "$1").replace(/[^a-z0-9]+/g, " ").trim();
  }

  var PERSONAL_ATTACK = /\b(?:(?:seus?|suas?|voce e|tu e|vc e)\s+(?:inutil|burro|idiota|imbecil|bosta|otario|lixo|merda|animal|arrombado|paspalho|incompetente)\w*|cala (?:a )?boca|vai (?:se f[ou]der|tomar no cu)|nem pra isso (?:tu|vc|voce) presta|nao sabe fazer nada)\b/;
  var SWEAR = /\b(?:porra|merda|caralho|bosta|cacete|desgraca|puta|foda|bucetona?\w*)\b/;
  var CONTEMPT = /\b(?:que (?:lixo|bosta|merda|vergonha)|ta uma (?:bosta|merda)|ja falei|quantas vezes|eu fui claro|presta atencao|de novo isso|anda logo)\b/;
  var PRESSURE = /\b(?:agora mesmo|imediatamente|pra ontem|sem desculpas|sem conversa|de uma vez|to esperando|ta esperando o que|cade logo|faz logo|anda logo)\b/;
  var ACTION = /\b(?:faz|faca|fazer|cria|crie|criar|corrige|corrija|corrigir|conserta|conserte|arruma|arrume|resolve|resolva|muda|mude|melhora|melhore|refaz|refaca|entrega|entregue|manda|mande|mostra|mostre|adiciona|adicione|remove|remova|ajusta|ajuste|explica|explique|escreve|escreva|ajeita|ajeite|atualiza|atualize|implementa|implemente|configura|configure|liste|analise|revise|traduza|resuma|compare|gere|teste|calcule|descreva|retorne|substitua|quero|preciso|pode|consegue|qual|quando|onde|como|cade)\b/;
  var VAGUE = /\b(?:faz|faca|conserta|conserte|arruma|arrume|resolve|resolva|corrige|corrija|melhora|melhore|refaz|refaca|ajeita|ajeite|muda|mude)\s+(?:logo\s+)?(?:(?:essa|esse|um|uma|a|o|essa porcaria d[eo])\s+)?(?:isso|isto|aquilo|ai|tudo|troco|negocio|coisa|merda|porra|bucetona)\b|\b(?:faz|faca)\s+(?:logo\s+)?(?:funcionar|direito)|\b(?:mostra|manda|faca|faz|cria|crie)\s+(?:ai\s+)?(?:alguma|qualquer) coisa\b/;
  var UNBOUNDED = /\b(?:(?:refaz|refaca|muda|mude|arruma|arrume|corrige|corrija|entrega|entregue) tudo|(?:app|jogo|site|sistema|projeto|produto|plataforma) (?:inteir[oa]|complet[oa]|do zero)|faz tudo)\b/;
  var EDIT = /\b(?:altera|altere|muda|mude|corrige|corrija|troca|troque|reescreve|reescreva|refaz|refaca|substitua)\b/;
  var KEEP_UNCHANGED = /\b(?:sem (?:mudar|alterar) nada|(?:mantem|mantenha|deixa|deixe) (?:tudo|exatamente) igual)\b(?! (?:alem|fora|exceto|mais))/;

  function summarizeSignals(signals) {
    var total = 0;
    var strongest = null;
    signals.forEach(function (signal) {
      if (!signal[0]) return;
      total += signal[1];
      if (!strongest || signal[1] > strongest[1]) strongest = signal;
    });
    return { value: Math.min(100, total), reaction: strongest ? strongest[2] : "" };
  }

  function scoreLocal(query) {
    var text = (query || "").trim();
    var unquoted = text.replace(/“[^”]*”|"[^"]*"|‘[^’]*’|'[^'\n]*'|`[^`]*`/g, " ");
    var tone = normalize(unquoted);
    var letters = unquoted.match(/\p{L}/gu) || [];
    var uppercase = letters.filter(function (letter) {
      return letter === letter.toUpperCase() && letter !== letter.toLowerCase();
    }).length;
    var shouting = letters.length >= 6 && uppercase / letters.length >= .72;

    var attack = PERSONAL_ATTACK.test(tone);
    var pressure = PRESSURE.test(tone);
    var aggression = summarizeSignals([
      [attack, 48, "O xingamento veio com destinatário até demais."],
      [SWEAR.test(tone), 30, "Recebi o pedido com palavrão de brinde."],
      [CONTEMPT.test(tone), 25, "Já chegou me cobrando como se eu tivesse sumido da live."],
      [pressure, 22, "Esse prazo aí veio no grito."],
      [shouting, 18, "Dá pra ouvir esse caps lock daqui."],
      [/[!?]{2,}/.test(unquoted), 10, "Até a pontuação perdeu a paciência."]
    ]);

    // Insultos e cobranças enchem a frase, mas não dão detalhes da tarefa.
    var taskText = tone.replace(PERSONAL_ATTACK, " ");
    var hasTask = ACTION.test(taskText) || EDIT.test(taskText);
    var hasDetails = /\b(?:por exemplo|deve (?:ter|conter|retornar|mostrar)|o resultado|criterios? de|passos? para)\b/.test(tone);
    var explainsReference = /\b(?:isso|isto|aquilo)\s*:/i.test(unquoted);
    var vague = !explainsReference && VAGUE.test(tone);
    var unbounded = !hasDetails && UNBOUNDED.test(tone);
    var keepUnchanged = KEEP_UNCHANGED.exec(taskText);
    var contradicts = keepUnchanged && EDIT.test(taskText.slice(0, keepUnchanged.index));
    var requestWords = (normalize(text).match(/[a-z0-9]+/g) || []).filter(function (word) {
      return !/^(?:mano|claude|porra|caralho|merda|bosta|agora|logo|seu|sua|inutil|burro|idiota|voce|vc|tu|o|a|de|do|da|pra|ai|e|com)$/.test(word);
    });
    var confusion = summarizeSignals([
      [Boolean(tone) && !hasTask, 40, "Qual é a tarefa, mano? Recebi a mensagem, mas cadê o pedido?"],
      [vague, 45, "Esse pedido ficou no “faz aí”. Qual é o problema e o que você quer ver pronto?"],
      [unbounded, 32, "Esse pedido ficou grande demais pra um “faz aí”. O que tem que sair primeiro?"],
      [contradicts, 45, "Você tá pedindo pra eu mudar e manter tudo igual. Qual vale?"],
      [hasTask && requestWords.length < 5, 18, "Me dá um detalhe do que você espera ver pronto."],
      [(vague || unbounded) && pressure, 24, "O prazo tá definido. O que tem que ficar pronto, nem tanto."]
    ]);

    // O bônus de combinação exige os dois sinais para chegar à pane.
    var overlap = Math.min(aggression.value, confusion.value);
    var score = Math.round(.35 * aggression.value + .35 * confusion.value + .3 * overlap);
    return { score: score, aggression: aggression, confusion: confusion };
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
    if (band === "cold") return "O Claude ainda tá longe de pedir arrego.";
    if (band === "warm") return "Já deu trabalho. O Claude ainda tá processando.";
    if (band === "hot") return "O chat já reparou que o Claude tá lagando.";
    return "Cobrança no talo e o Claude ainda tentando entender a tarefa.";
  }

  function moodFor(score) {
    var band = bandFor(score);
    if (band === "cold") return "paciência: entediado";
    if (band === "warm") return "paciência: desconfiado";
    if (band === "hot") return "paciência: 12%";
    return "paciência: foi de Viper";
  }

  function replyFor(result) {
    var tone = result.aggression.reaction || "Até parece que a live tá em modo calmo.";
    var task = result.confusion.reaction || "Dá pra entender o que você quer que eu faça.";
    var band = bandFor(result.score);
    var lead = band === "gato" ? "OOOO CRAUD! A live já caiu." :
      band === "hot" ? "Mano, o contexto já começou a lagar." : "Bora lá.";
    return [lead, tone, task].join(" ");
  }

  function renderGauge(result) {
    var measured = Boolean(result);
    var pct = measured ? result.score : 0;
    aggressionNum.textContent = measured ? result.aggression.value + "%" : "–";
    confusionNum.textContent = measured ? result.confusion.value + "%" : "–";
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

  function stopScreenEffect() {
    if (!screenEffect) return;
    screenEffect.tracks.forEach(function (track) { track.cancel(); });
    screenEffect = null;
    cancelAnimationFrame(effectFrame);
    scene.classList.remove("is-collapsing");
    delete overlay.dataset.stage;
    overlay.style.removeProperty("--progress");
    overlay.hidden = true;
    craudAudio.pause();
  }

  function clipDuration() {
    return Number.isFinite(craudAudio.duration) && craudAudio.duration > 0
      ? craudAudio.duration
      : CLIP_FALLBACK_SECONDS;
  }

  function switchToSilentEffect(effect) {
    if (screenEffect !== effect || !effect.withAudio) return;
    effect.withAudio = false;
    effect.startedAt = performance.now() - effect.progress * clipDuration() * 1000 / effect.rate;
    craudAudio.pause();
  }

  function createCollapseTracks(score) {
    // Frações do clipe, na ordem em que as peças se soltam do layout.
    var pieces = [
      { selector: ".prompt-panel", drop: .22, returnAt: .735, angle: -17 },
      { selector: ".meter-panel", drop: .265, returnAt: .776, angle: 20 },
      { selector: ".claude-panel", drop: .32, returnAt: .824, angle: -12 },
      { selector: "footer", drop: .355, returnAt: .92, angle: 6 },
      { selector: ".intro", drop: .363, returnAt: .694, angle: 11 },
      { selector: ".topbar", drop: .405, returnAt: .654, angle: -9 }
    ];
    var intensity = score === 100 ? 1 : .76;

    return pieces.map(function (piece) {
      var element = scene.querySelector(piece.selector);
      var angle = piece.angle * intensity;
      var direction = Math.sign(angle);
      var settled = "translate3d(0,0,0) rotate(0deg)";
      var fallen = "translate3d(" + direction * 14 + "vw,115vh,0) rotate(" + angle * 1.9 + "deg)";
      var track = element.animate([
        { offset: 0, transform: settled, opacity: 1, visibility: "visible" },
        { offset: .031, transform: "translateY(" + -6 * intensity + "px) rotate(" + direction * .9 + "deg)" },
        { offset: .075, transform: settled },
        { offset: piece.drop, transform: settled, easing: "ease-out" },
        { offset: piece.drop + .045, transform: "translate3d(" + direction * 8 + "px,18px,0) rotate(" + angle * .32 + "deg)",
          opacity: 1, easing: "cubic-bezier(.55,.02,.85,.55)" },
        { offset: piece.drop + .135, transform: fallen, opacity: 0, visibility: "hidden" },
        { offset: piece.returnAt, transform: "translate3d(0,46px,0) rotate(" + -angle * .18 + "deg) scale(.98)",
          opacity: 0, visibility: "hidden", easing: "cubic-bezier(.16,.75,.3,1)" },
        { offset: piece.returnAt + .047, transform: "translateY(-3px)", opacity: 1, visibility: "visible", easing: "ease-out" },
        { offset: piece.returnAt + .075, transform: settled, opacity: 1, visibility: "visible" },
        { offset: 1, transform: settled, opacity: 1, visibility: "visible" }
      ], { duration: 1000, fill: "both" });

      track.pause();
      track.currentTime = 0;
      return track;
    });
  }

  function updateScreenEffect(now) {
    var effect = screenEffect;
    var duration = clipDuration();
    var position = effect.withAudio ? craudAudio.currentTime : (now - effect.startedAt) * effect.rate / 1000;
    var progress = Math.min(1, Math.max(0, position / duration));
    effect.progress = progress;
    if (!effect.withAudio && progress >= 1) {
      stopScreenEffect();
      return;
    }

    var stage = progress < .54 ? "impact" : progress < .654 ? "outage" : "reboot";
    if (stage !== overlay.dataset.stage) {
      overlay.dataset.stage = stage;
      if (stage === "impact") signalMessage.textContent = "A página caiu";
      if (stage === "outage") signalMessage.textContent = "Sem sinal";
      if (stage === "reboot") signalMessage.textContent = "Reconectando à live";
    }

    effect.tracks.forEach(function (track) { track.currentTime = progress * 1000; });
    overlay.style.setProperty("--progress", progress.toFixed(4));
    effectFrame = requestAnimationFrame(updateScreenEffect);
  }

  function startScreenEffect(score) {
    var effect = {
      withAudio: soundOn,
      rate: score === 100 ? 1.12 : 1,
      startedAt: performance.now(),
      progress: 0,
      tracks: createCollapseTracks(score)
    };
    screenEffect = effect;
    scene.classList.add("is-collapsing");
    overlay.hidden = false;

    if (effect.withAudio) {
      try {
        craudAudio.playbackRate = effect.rate;
        craudAudio.currentTime = 0;
        craudAudio.play().catch(function () { switchToSilentEffect(effect); });
      } catch (error) {
        switchToSilentEffect(effect);
      }
    }

    updateScreenEffect(performance.now());
  }

  craudAudio.addEventListener("ended", function () {
    if (screenEffect && screenEffect.withAudio && craudAudio.ended) stopScreenEffect();
  });
  craudAudio.addEventListener("error", function () {
    if (screenEffect && craudAudio.error) switchToSilentEffect(screenEffect);
  });

  document.addEventListener("keydown", function (event) {
    if (screenEffect && (event.key === "Escape" || event.key === "Tab")) stopScreenEffect();
  });

  function measure() {
    stopScreenEffect();
    var query = (input.value || "").trim();
    lastQuery = query;
    copyStatus.textContent = "";

    if (!query) {
      renderGauge(null);
      scoreLabel.textContent = "CADÊ O PROMPT?";
      scoreSub.textContent = "O Claude faz muita coisa, mas adivinhar ainda não.";
      setReply("Mano, escreve alguma coisa antes. Até o Viper invisível tinha mais conteúdo que esse campo.", true, "paciência: esperando o prompt");
      input.setAttribute("aria-invalid", "true");
      input.focus();
      return;
    }

    input.setAttribute("aria-invalid", "false");
    var result = scoreLocal(query);
    renderGauge(result);
    setReply(replyFor(result), false, moodFor(result.score));
    btnCopyPrompt.disabled = false;
    if (bandFor(result.score) === "gato") startScreenEffect(result.score);
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
    stopScreenEffect();
    input.value = "";
    lastQuery = "";
    input.setAttribute("aria-invalid", "false");
    updateCharCount();
    renderGauge(null);
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
    if (!soundOn && screenEffect) switchToSilentEffect(screenEffect);
    try {
      if (window.localStorage) window.localStorage.setItem("elgato-sound", soundOn ? "on" : "off");
    } catch (error) { /* ignora */ }
    syncSoundButton();
  });
  syncSoundButton();
  btnCopyPrompt.addEventListener("click", function () {
    copyText((input.value || "").trim() || lastQuery, btnCopyPrompt, "Prompt");
  });

  updateCharCount();
  renderGauge(null);
  window.ElGato = { scoreLocal: scoreLocal, replyFor: replyFor };
})();
