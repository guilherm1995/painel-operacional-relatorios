// Interações do site: lupa nas imagens, upload por arraste, log ao vivo e o
// alerta de garantias do bot.

(function () {
  "use strict";

  // ---------- lupa (clicar na imagem para ampliar) ----------
  const lupa = document.getElementById("lupa");
  if (lupa) {
    const alvo = lupa.querySelector("img");
    document.addEventListener("click", function (ev) {
      // pega tanto a moldura branca (Painel) quanto a escura (Backlog)
      const img = ev.target.closest(".painel-carta img");
      if (img) {
        alvo.src = img.src;
        lupa.classList.add("aberta");
        return;
      }
      if (ev.target.closest(".lupa")) lupa.classList.remove("aberta");
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") lupa.classList.remove("aberta");
    });
  }

  // ---------- upload da extração ----------
  const solta = document.getElementById("solta");
  if (solta) {
    const campo = document.getElementById("arquivo");
    const botaoEscolher = document.getElementById("escolher");
    const nome = document.getElementById("nome-arquivo");
    const acao = document.getElementById("acao");
    const enviar = document.getElementById("enviar");

    function mostrar() {
      if (campo.files.length) {
        const arq = campo.files[0];
        nome.textContent = arq.name + " · " + (arq.size / 1048576).toFixed(1) + " MB";
        acao.style.display = "block";
      }
    }

    botaoEscolher.addEventListener("click", () => campo.click());
    campo.addEventListener("change", mostrar);

    ["dragenter", "dragover"].forEach(function (evento) {
      solta.addEventListener(evento, function (ev) {
        ev.preventDefault();
        solta.classList.add("sobre");
      });
    });
    ["dragleave", "drop"].forEach(function (evento) {
      solta.addEventListener(evento, function (ev) {
        ev.preventDefault();
        solta.classList.remove("sobre");
      });
    });
    solta.addEventListener("drop", function (ev) {
      if (ev.dataTransfer.files.length) {
        campo.files = ev.dataTransfer.files;
        mostrar();
      }
    });

    // gerar as 4 telas leva alguns segundos - avisa que está trabalhando
    solta.addEventListener("submit", function () {
      if (enviar) {
        enviar.disabled = true;
        enviar.textContent = "Gerando as telas…";
      }
    });
  }

  // ---------- menu mobile (hamburguer) ----------
  const menuAlternar = document.getElementById("menu-alternar");
  const menu = document.getElementById("menu");
  if (menuAlternar && menu) {
    menuAlternar.addEventListener("click", function () {
      const aberto = menu.classList.toggle("aberto");
      menuAlternar.classList.toggle("aberto", aberto);
      menuAlternar.setAttribute("aria-expanded", aberto ? "true" : "false");
    });
    // clicar num link ou aumentar a tela de volta pro desktop fecha o painel
    menu.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        menu.classList.remove("aberto");
        menuAlternar.classList.remove("aberto");
        menuAlternar.setAttribute("aria-expanded", "false");
      });
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 780) {
        menu.classList.remove("aberto");
        menuAlternar.classList.remove("aberto");
        menuAlternar.setAttribute("aria-expanded", "false");
      }
    });
  }

  // ---------- log ao vivo ----------
  const log = document.getElementById("log");
  if (log) {
    const colado = () => log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    let seguir = true;
    log.scrollTop = log.scrollHeight;
    log.addEventListener("scroll", function () { seguir = colado(); });

    setInterval(function () {
      fetch("/backlog/log")
        .then((r) => r.json())
        .then(function (dados) {
          if (!dados.linhas || !dados.linhas.length) return;
          log.innerHTML = dados.linhas
            .map(function (l) {
              const div = document.createElement("div");
              div.className = "linha " + l.nivel;
              div.textContent = l.texto;
              return div.outerHTML;
            })
            .join("");
          if (seguir) log.scrollTop = log.scrollHeight;
        })
        .catch(function () { /* servidor fora do ar: tenta de novo no próximo ciclo */ });
    }, 10000);
  }

  // ---------- alerta de garantias do bot ----------
  // O bot toca um som e manda no grupo quando acha uma garantia, mas isso
  // acontece na máquina DELE. Aqui o site relê o mesmo registro e avisa quem
  // estiver com qualquer página aberta: som, cartão na tela e notificação do
  // navegador. Não reavalia nada -- só relata o que o bot já decidiu.
  if (document.querySelector("header.topo")) {
    const INTERVALO = 20000;
    const CHAVE = "operacional.garantias.avisadas";
    const tituloOriginal = document.title;

    // localStorage some em janela anônima e em alguns navegadores embarcados;
    // sem o plano B o alerta quebraria calado justamente onde ninguém olha.
    let memoria = [];
    const guarda = {
      ler: function () {
        try {
          return JSON.parse(localStorage.getItem(CHAVE) || "[]");
        } catch (e) { return memoria; }
      },
      gravar: function (ids) {
        memoria = ids;
        try { localStorage.setItem(CHAVE, JSON.stringify(ids)); } catch (e) { /* fica só em memória */ }
      },
    };

    // O navegador só deixa tocar áudio depois que a pessoa interagiu com a
    // página. Destravamos no primeiro clique/tecla: um play mudo e imediato
    // pausa deixa o elemento liberado para tocar sozinho depois.
    const versao = (document.currentScript && document.currentScript.src.split("?v=")[1]) || "";
    const som = new Audio("/static/garantia.mp3" + (versao ? "?v=" + versao : ""));
    som.preload = "auto";
    let liberado = false;

    function destravar() {
      if (liberado) return;
      const antes = som.muted;
      som.muted = true;
      const t = som.play();
      if (t && t.then) {
        t.then(function () {
          som.pause();
          som.currentTime = 0;
          som.muted = antes;
          liberado = true;
        }).catch(function () { som.muted = antes; });
      }
      // pedir a permissão da notificação junto do gesto: fora dele o
      // navegador ignora (ou penaliza) o pedido
      if ("Notification" in window && Notification.permission === "default") {
        try { Notification.requestPermission(); } catch (e) { /* navegador antigo */ }
      }
    }
    ["pointerdown", "keydown"].forEach(function (ev) {
      document.addEventListener(ev, destravar, { once: false, passive: true });
    });

    let pilha = document.getElementById("alertas-garantia");
    if (!pilha) {
      pilha = document.createElement("div");
      pilha.id = "alertas-garantia";
      pilha.className = "alertas-garantia";
      pilha.setAttribute("role", "alert");
      pilha.setAttribute("aria-live", "assertive");
      document.body.appendChild(pilha);
    }

    function marcarTitulo() {
      const n = pilha.querySelectorAll(".alerta-garantia").length;
      document.title = n ? "(" + n + ") " + tituloOriginal : tituloOriginal;
    }

    // Se o navegador barrou o áudio, cada cartão ganha o botão que libera --
    // e é o clique nele que destrava também os alertas seguintes.
    function botaoAtivarSom(div) {
      const acoes = div.querySelector(".alerta-acoes");
      if (!acoes || acoes.querySelector(".alerta-ativar")) return;
      const ativar = document.createElement("button");
      ativar.type = "button";
      ativar.className = "alerta-link alerta-ativar";
      ativar.textContent = "🔊 Ativar som";
      ativar.addEventListener("click", function () {
        liberado = false;
        destravar();
        som.play().catch(function () { /* segue barrado; o cartão já avisou */ });
        ativar.remove();
      });
      acoes.insertBefore(ativar, acoes.firstChild);
    }

    function cartao(item) {
      const div = document.createElement("div");
      div.className = "alerta-garantia";

      const topo = document.createElement("div");
      topo.className = "alerta-topo";
      topo.textContent = "GARANTIA · " + item.unidade;
      div.appendChild(topo);

      const linhas = [
        "Contrato " + item.contrato + " · " + item.cliente,
        item.bairro + (item.telefones ? " · " + item.telefones : ""),
      ];
      if (item.tipo_anterior) {
        linhas.push("Anterior: " + item.tipo_anterior +
          (item.dias_aging !== null && item.dias_aging !== undefined
            ? " há " + item.dias_aging + " dia(s)" : ""));
      }
      linhas.forEach(function (texto) {
        const p = document.createElement("div");
        p.className = "alerta-linha";
        p.textContent = texto;
        div.appendChild(p);
      });

      const acoes = document.createElement("div");
      acoes.className = "alerta-acoes";

      const ver = document.createElement("a");
      ver.href = "/garantias";
      ver.className = "alerta-link";
      ver.textContent = "Ver garantias";
      acoes.appendChild(ver);

      const fechar = document.createElement("button");
      fechar.type = "button";
      fechar.className = "alerta-fechar";
      fechar.setAttribute("aria-label", "Dispensar");
      fechar.textContent = "×";
      fechar.addEventListener("click", function () {
        div.remove();
        marcarTitulo();
      });
      acoes.appendChild(fechar);

      div.appendChild(acoes);
      return div;
    }

    function avisar(novos) {
      // O visual não espera pelo áudio: o cartão sobe na hora e o som corre em
      // paralelo. Se a promessa do play() for recusada, aí sim cada cartão
      // ganha o botão de liberar.
      const cartoes = novos.map(function (item) {
        const div = cartao(item);
        pilha.appendChild(div);
        return div;
      });
      marcarTitulo();

      if ("Notification" in window && Notification.permission === "granted") {
        novos.forEach(function (item) {
          try {
            new Notification("GARANTIA · " + item.unidade, {
              body: "Contrato " + item.contrato + " · " + item.cliente + "\n" + item.bairro,
              icon: "/static/favicon.png",
              tag: "garantia-" + item.id,
            });
          } catch (e) { /* alguns navegadores exigem service worker */ }
        });
      }

      let tocando;
      try {
        som.currentTime = 0;
        tocando = som.play();
      } catch (e) {
        tocando = Promise.reject(e);
      }
      if (tocando && tocando.catch) {
        tocando.catch(function () { cartoes.forEach(botaoAtivarSom); });
      }
    }

    let primeira = true;
    function verificar() {
      fetch("/alertas/garantias", { headers: { "Accept": "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (dados) {
          if (!dados || !dados.disponivel) return;
          const ids = dados.itens.map(function (i) { return i.id; });

          // Na primeira volta só memoriza: sem isso, abrir o site dispararia o
          // som para todas as garantias que o bot já tinha notificado antes.
          if (primeira) {
            primeira = false;
            guarda.gravar(ids);
            return;
          }

          const vistas = guarda.ler();
          const novos = dados.itens.filter(function (i) { return vistas.indexOf(i.id) === -1; });
          // Grava só o que veio agora: o bot poda o arquivo em 45 dias, então
          // a lista memorizada acompanha e não cresce para sempre.
          guarda.gravar(ids);
          if (novos.length) avisar(novos);
        })
        .catch(function () { /* site fora do ar: tenta de novo no próximo ciclo */ });
    }

    verificar();
    setInterval(verificar, INTERVALO);
  }
})();
