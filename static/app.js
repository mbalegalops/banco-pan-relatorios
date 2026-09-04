(() => {
  const STATUS_LABEL = { running: "Em execução", success: "Concluída", error: "Erro", cancelled: "Cancelada" };
  const STATUS_COLOR = { running: "#0b5cad", success: "#1a7f37", error: "#c0392b", cancelled: "#9a6b00" };

  const el = (id) => document.getElementById(id);

  const listBody = el("listBody");
  const btnExecutar = el("btnExecutar");
  const btnCredenciais = el("btnCredenciais");
  const btnAtualizar = el("btnAtualizar");
  const btnCopiar = el("btnCopiar");
  const btnAbrir = el("btnAbrir");
  const warning = el("warning");
  const statusBar = el("statusBar");
  const logBox = el("logBox");

  const emptyLabel = el("emptyLabel");
  const detailContent = el("detailContent");
  const detTitle = el("detTitle");
  const detStatus = el("detStatus");
  const reportButtons = el("reportButtons");
  const detMeta = el("detMeta");
  const msgBox = el("msgBox");

  const modalOverlay = el("modalOverlay");
  const modalYesNo = el("modalYesNo");
  const modalTitle = el("modalTitle");
  const modalMsg = el("modalMsg");
  const modalYes = el("modalYes");
  const modalNo = el("modalNo");

  const modalCredenciais = el("modalCredenciais");
  const credEmail = el("credEmail");
  const credSenha = el("credSenha");
  const credSenhaHint = el("credSenhaHint");
  const credError = el("credError");
  const credSalvar = el("credSalvar");
  const credCancelar = el("credCancelar");
  const btnToggleSenha = el("btnToggleSenha");

  let execucoes = [];
  let selectedId = null;
  let liveExecucaoId = null;
  let running = false;
  let statusTimer = null;

  // ------------------------------------------------------------- helpers

  function flash(msg, color) {
    statusBar.textContent = msg;
    statusBar.style.color = color || "#111";
    if (statusTimer) clearTimeout(statusTimer);
    statusTimer = setTimeout(() => { statusBar.textContent = ""; }, 4200);
  }

  function fetchJSON(url, options) {
    return fetch(url, options).then(async (r) => {
      if (!r.ok) {
        let msg = r.statusText;
        try { const body = await r.json(); msg = body.detail || msg; } catch (e) {}
        throw new Error(msg);
      }
      return r.status === 204 ? null : r.json();
    });
  }

  function escapeHtml(s) {
    return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  // ------------------------------------------------------------- render

  function renderList() {
    listBody.innerHTML = "";
    if (execucoes.length === 0) {
      listBody.innerHTML = '<div class="list-empty">Nenhuma execução ainda.</div>';
      return;
    }
    for (const r of execucoes) {
      const status = r.id === liveExecucaoId && running ? "running" : r.status;
      const row = document.createElement("div");
      row.className = "list-row status-" + status;
      if (r.id === selectedId) row.classList.add("selected");
      row.innerHTML = `
        <div>Execução #${r.id}</div>
        <div>${r.inicio || "-"}</div>
        <div>${r.usuario || "-"}</div>
        <div>${STATUS_LABEL[status] || status}</div>`;
      row.addEventListener("click", () => selectExecucao(r.id));
      listBody.appendChild(row);
    }
  }

  function renderLogLines(container, linhas) {
    for (const l of linhas) {
      const line = document.createElement("div");
      line.className = "log-line";
      const levelClass = "log-level-" + (l.lvl || "");
      line.innerHTML = `
        <span class="log-time">${l.t || ""}</span>
        <span class="log-level ${levelClass}">${l.lvl || ""}</span>
        <span class="log-msg">${escapeHtml(l.msg || "")}</span>`;
      container.appendChild(line);
    }
  }

  function renderReportButtons(r) {
    reportButtons.innerHTML = "";
    if (!r.relatorios.length) {
      reportButtons.hidden = true;
      return;
    }
    for (const nome of r.relatorios) {
      const link = document.createElement("a");
      link.className = "btn btn-small";
      link.href = `/api/relatorios/${r.id}/${encodeURIComponent(nome)}`;
      link.download = nome;
      link.textContent = `📄 ${nome}`;
      reportButtons.appendChild(link);
    }
    reportButtons.hidden = false;
  }

  function renderDetail() {
    const r = execucoes.find((e) => e.id === selectedId);
    if (!r) {
      emptyLabel.hidden = false;
      detailContent.hidden = true;
      return;
    }
    emptyLabel.hidden = true;
    detailContent.hidden = false;

    const isLive = r.id === liveExecucaoId;
    const status = isLive && running ? "running" : r.status;
    detTitle.textContent = `Execução #${r.id}`;
    detStatus.textContent = `● ${STATUS_LABEL[status] || status}`;
    detStatus.style.color = STATUS_COLOR[status] || "#666";

    renderReportButtons(r);

    let meta = `Iniciada em ${r.inicio || "-"}`;
    if (r.usuario && r.usuario !== "-") meta += ` · Usuário: ${r.usuario}`;
    if (r.fim) meta += ` · Concluída em ${r.fim}`;
    detMeta.textContent = meta;

    if (!isLive && status === "error" && r.erro) {
      msgBox.hidden = false;
      msgBox.textContent = r.erro;
    } else {
      msgBox.hidden = true;
    }

    btnAbrir.href = `/api/logs/${r.id}/download`;
  }

  function selectExecucao(id) {
    selectedId = id;
    renderList();
    renderDetail();
    logBox.innerHTML = "";
    if (id === liveExecucaoId) {
      return; // logs ao vivo já vêm pelo WS
    }
    fetchJSON(`/api/logs/${id}`).then((data) => {
      if (!data.encontrado) {
        logBox.innerHTML = '<div class="log-line"><span class="log-msg">Log não encontrado para essa execução.</span></div>';
        return;
      }
      renderLogLines(logBox, data.linhas);
      logBox.scrollTop = logBox.scrollHeight;
    });
  }

  function refreshExecucoes(selectFirst) {
    return fetchJSON("/api/execucoes").then((data) => {
      execucoes = data.execucoes;
      liveExecucaoId = data.live_execucao_id;
      running = data.running;
      warning.hidden = data.config_ok;
      if (!data.config_ok) {
        warning.textContent = 'Credenciais do eLaw não configuradas — clique em "Credenciais" antes de executar.';
      }
      atualizarBotaoExecutar();
      if (selectFirst && selectedId === null && execucoes.length > 0) {
        selectExecucao(execucoes[0].id);
      } else {
        renderList();
        renderDetail();
      }
    });
  }

  function atualizarBotaoExecutar() {
    btnExecutar.textContent = running ? "Cancelar" : "▶ Executar agora";
    btnExecutar.classList.toggle("btn-primary", !running);
  }

  // ------------------------------------------------------------- modais

  function askYesNo(titulo, mensagem) {
    return new Promise((resolve) => {
      modalTitle.textContent = titulo;
      modalMsg.textContent = mensagem;
      modalYesNo.hidden = false;
      modalCredenciais.hidden = true;
      modalOverlay.hidden = false;
      const cleanup = () => {
        modalOverlay.hidden = true;
        modalYes.onclick = null;
        modalNo.onclick = null;
      };
      modalYes.onclick = () => { cleanup(); resolve(true); };
      modalNo.onclick = () => { cleanup(); resolve(false); };
    });
  }

  function closeModal() {
    modalOverlay.hidden = true;
  }

  modalOverlay.addEventListener("click", (event) => {
    if (event.target === modalOverlay) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modalOverlay.hidden) closeModal();
  });

  function openCredenciaisModal() {
    credError.textContent = "";
    credSenha.value = "";
    fetchJSON("/api/credenciais").then((data) => {
      credEmail.value = data.email || "";
      credSenhaHint.textContent = data.tem_senha ? "Deixe em branco para manter a atual" : "Digite a senha";
      modalYesNo.hidden = true;
      modalCredenciais.hidden = false;
      modalOverlay.hidden = false;
    });
  }

  credCancelar.onclick = () => closeModal();

  btnToggleSenha.addEventListener("click", (e) => {
    e.preventDefault();
    if (credSenha.type === "password") {
      credSenha.type = "text";
      btnToggleSenha.textContent = "🔓";
    } else {
      credSenha.type = "password";
      btnToggleSenha.textContent = "👁";
    }
  });

  credSalvar.onclick = () => {
    const email = credEmail.value.trim();
    const senha = credSenha.value;
    if (!email) {
      credError.textContent = "Preencha o e-mail.";
      return;
    }
    fetchJSON("/api/credenciais", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, senha: senha || null }),
    })
      .then(() => {
        closeModal();
        flash("Credenciais salvas.", "#0b5cad");
        refreshExecucoes(false);
      })
      .catch((e) => { credError.textContent = e.message; });
  };

  btnCredenciais.addEventListener("click", openCredenciaisModal);

  // ------------------------------------------------------------- ações

  btnExecutar.addEventListener("click", () => {
    if (running) {
      askYesNo("Cancelar execução", "Cancelar a execução em andamento? Isso interrompe tudo que estiver rodando agora.")
        .then((sim) => {
          if (!sim) return;
          fetchJSON("/api/cancelar", { method: "POST" })
            .then(() => flash("Cancelamento solicitado, aguarde a interrupção...", "#9a6b00"))
            .catch((e) => flash(e.message, "#c0392b"));
        });
      return;
    }
    fetchJSON("/api/executar", { method: "POST" })
      .then((data) => {
        flash("Execução iniciada.", "#0b5cad");
        liveExecucaoId = data.execucao_id;
        selectedId = data.execucao_id;
        logBox.innerHTML = "";
        refreshExecucoes(false);
      })
      .catch((e) => flash(e.message, "#c0392b"));
  });

  btnAtualizar.addEventListener("click", () => refreshExecucoes(false));

  btnCopiar.addEventListener("click", () => {
    const texto = Array.from(logBox.querySelectorAll(".log-line"))
      .map((line) => line.textContent.replace(/\s+/g, " ").trim())
      .join("\n");
    navigator.clipboard.writeText(texto).catch(() => {});
    flash("Log copiado para a área de transferência.", "#0b5cad");
  });

  // ------------------------------------------------------------- websocket

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      const wasRunning = running;
      liveExecucaoId = data.live_execucao_id;
      running = data.running;

      if (data.novas_linhas && data.novas_linhas.length && selectedId === liveExecucaoId) {
        const estavaNoFim = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 4;
        renderLogLines(logBox, data.novas_linhas);
        if (estavaNoFim) logBox.scrollTop = logBox.scrollHeight;
      }

      if (liveExecucaoId != null && data.live_downloads) {
        const r = execucoes.find((e) => e.id === liveExecucaoId);
        if (r) {
          const nomes = Object.keys(data.live_downloads);
          if (nomes.length && nomes.some((n) => !r.relatorios.includes(n))) {
            r.relatorios = nomes;
          }
        }
      }

      atualizarBotaoExecutar();

      if (wasRunning && !running) {
        refreshExecucoes(false).then(() => {
          if (selectedId === liveExecucaoId) selectExecucao(selectedId);
        });
      } else if (selectedId === liveExecucaoId) {
        renderList();
        renderDetail();
      }
    };

    ws.onclose = () => setTimeout(connectWs, 1000);
    ws.onerror = () => ws.close();
  }

  // ------------------------------------------------------------- Agendamentos

  const tabExecutacoes = el("tabExecutacoes");
  const tabAgendamentos = el("tabAgendamentos");
  const execucoesTab = el("execucoesTab");
  const agendamentosTab = el("agendamentosTab");
  const btnNovaAgenda = el("btnNovaAgenda");
  const scheduleListBody = el("scheduleListBody");
  const modalAgenda = el("modalAgenda");
  const scheduleHour = el("scheduleHour");
  const scheduleMinute = el("scheduleMinute");
  const dayChecks = document.querySelectorAll(".day-check");
  const scheduleEnabled = el("scheduleEnabled");
  const scheduleError = el("scheduleError");
  const scheduleSave = el("scheduleSave");
  const scheduleCancel = el("scheduleCancel");

  let schedules = [];
  let editingScheduleId = null;

  // Preencher seletor de horas
  for (let h = 0; h < 24; h++) {
    const opt = document.createElement("option");
    opt.value = h;
    opt.textContent = `${String(h).padStart(2, "0")}:00`;
    scheduleHour.appendChild(opt);
  }

  const DIAS_SEMANA = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"];

  function renderSchedules() {
    scheduleListBody.innerHTML = "";
    if (schedules.length === 0) {
      scheduleListBody.innerHTML = '<div class="list-empty">Nenhuma agenda. Clique em "+ Nova".</div>';
      return;
    }
    for (const s of schedules) {
      const row = document.createElement("div");
      row.className = "list-row";
      const hora = `${String(s.hour || 0).padStart(2, "0")}:${String(s.minute || 0).padStart(2, "0")}`;
      const dias = (s.days || []).map(d => DIAS_SEMANA[d]).join(", ") || "-";
      const status = s.enabled ? "Ativo" : "Inativo";

      const editBtn = document.createElement("button");
      editBtn.className = "btn btn-small";
      editBtn.textContent = "Editar";
      editBtn.onclick = (e) => { e.stopPropagation(); openScheduleModal(s); };

      const delBtn = document.createElement("button");
      delBtn.className = "btn btn-small";
      delBtn.textContent = "Deletar";
      delBtn.onclick = (e) => {
        e.stopPropagation();
        if (confirm("Deletar esta agenda?")) {
          fetchJSON(`/api/schedules/${s._id}`, { method: "DELETE" })
            .then(() => { flash("Agenda deletada", "#0b5cad"); refreshSchedules(); })
            .catch((e) => flash(e.message, "#c0392b"));
        }
      };

      row.innerHTML = `
        <div>${hora}</div>
        <div>${dias}</div>
        <div>${status}</div>
        <div></div>`;
      row.lastChild.appendChild(editBtn);
      row.lastChild.appendChild(delBtn);
      scheduleListBody.appendChild(row);
    }
  }

  function openScheduleModal(schedule = null) {
    scheduleError.textContent = "";
    dayChecks.forEach(c => c.checked = false);

    if (schedule) {
      scheduleHour.value = schedule.hour || 8;
      scheduleMinute.value = schedule.minute || 0;
      (schedule.days || []).forEach(d => {
        const check = document.querySelector(`.day-check[value="${d}"]`);
        if (check) check.checked = true;
      });
      scheduleEnabled.checked = schedule.enabled || false;
      editingScheduleId = schedule._id;
    } else {
      scheduleHour.value = 8;
      scheduleMinute.value = 0;
      scheduleEnabled.checked = true;
      editingScheduleId = null;
      // Pré-selecionar seg-sex para nova agenda
      document.querySelectorAll(".day-check").forEach((c, i) => {
        if (i < 5) c.checked = true;
      });
    }

    modalYesNo.hidden = true;
    modalAgenda.hidden = false;
    modalOverlay.hidden = false;
  }

  function closeScheduleModal() {
    modalOverlay.hidden = true;
    modalAgenda.hidden = true;
  }

  function refreshSchedules() {
    return fetchJSON("/api/schedules").then((data) => {
      schedules = data.schedules || [];
      renderSchedules();
    }).catch((e) => flash(e.message, "#c0392b"));
  }

  scheduleSave.onclick = () => {
    const hour = parseInt(scheduleHour.value);
    const minute = parseInt(scheduleMinute.value);
    const days = Array.from(dayChecks).filter(c => c.checked).map(c => parseInt(c.value));
    const enabled = scheduleEnabled.checked;

    scheduleError.textContent = "";

    if (!days.length) {
      scheduleError.textContent = "Selecione pelo menos um dia";
      return;
    }

    const method = editingScheduleId ? "PUT" : "POST";
    const url = editingScheduleId ? `/api/schedules/${editingScheduleId}` : "/api/schedules";

    fetchJSON(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hour, minute, days, enabled }),
    })
      .then(() => {
        closeScheduleModal();
        flash("Agenda salva com sucesso", "#0b5cad");
        refreshSchedules();
      })
      .catch((e) => { scheduleError.textContent = e.message; });
  };

  scheduleCancel.onclick = closeScheduleModal;

  btnNovaAgenda.addEventListener("click", () => openScheduleModal());

  tabExecutacoes.addEventListener("click", () => {
    tabExecutacoes.classList.add("tab-active");
    tabAgendamentos.classList.remove("tab-active");
    execucoesTab.hidden = false;
    agendamentosTab.hidden = true;
  });

  tabAgendamentos.addEventListener("click", () => {
    tabAgendamentos.classList.add("tab-active");
    tabExecutacoes.classList.remove("tab-active");
    agendamentosTab.hidden = false;
    execucoesTab.hidden = true;
    refreshSchedules();
  });

  // ------------------------------------------------------------- boot

  refreshExecucoes(true);
  connectWs();
})();
