const API_BASE = "/api";

let token = localStorage.getItem("token") || null;
let currentUser = null;
let history = [];

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = options.headers || {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    logout("Session expired, please sign in again.");
    throw new Error("Session expired, please sign in again.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

// --- Auth ---

async function login(email, password) {
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  const res = await fetch(`${API_BASE}/auth/login`, { method: "POST", body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "Invalid email or password");
  }
  const data = await res.json();
  token = data.access_token;
  localStorage.setItem("token", token);
}

function logout(message = "") {
  token = null;
  currentUser = null;
  localStorage.removeItem("token");
  $("app-view").classList.add("hidden");
  $("login-view").classList.remove("hidden");
  $("login-error").textContent = message;
}

async function boot() {
  if (!token) return;
  try {
    currentUser = await api("/auth/me");
    $("user-email").textContent = currentUser.email;
    $("login-view").classList.add("hidden");
    $("app-view").classList.remove("hidden");
    $("users-nav-btn").classList.toggle("hidden", !currentUser.is_admin);
    loadDocuments();
  } catch (e) {
    logout();
  }
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    await login($("login-email").value, $("login-password").value);
    await boot();
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});

$("logout-btn").addEventListener("click", () => logout());

// --- Tabs ---

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((t) => t.classList.add("hidden"));
    btn.classList.add("active");
    $(`${btn.dataset.tab}-tab`).classList.remove("hidden");
    if (btn.dataset.tab === "documents") loadDocuments();
    if (btn.dataset.tab === "users") loadUsers();
  });
});

// --- Chat ---

function renderMessage(role, content, sources = []) {
  const wrap = document.createElement("div");
  wrap.className = `msg msg-${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = content;
  wrap.appendChild(bubble);

  if (sources.length) {
    const src = document.createElement("div");
    src.className = "sources";
    src.innerHTML =
      "Sources: " +
      sources
        .map((s) => `<span class="source-chip">${s.filename} #${s.chunk_index}</span>`)
        .join("");
    wrap.appendChild(src);
  }

  $("messages").appendChild(wrap);
  $("messages").scrollTop = $("messages").scrollHeight;
}

$("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("chat-input");
  const query = input.value.trim();
  if (!query) return;

  renderMessage("user", query);
  history.push({ role: "user", content: query });
  input.value = "";
  $("send-btn").disabled = true;

  const documentId = $("chat-doc-scope").value || null;
  const answerLanguage = $("chat-lang").value || null;

  try {
    const result = await api("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        history: history.slice(0, -1),
        document_id: documentId,
        answer_language: answerLanguage,
      }),
    });
    renderMessage("assistant", result.answer, result.sources);
    history.push({ role: "assistant", content: result.answer });
  } catch (err) {
    renderMessage("assistant", `Error: ${err.message}`);
  } finally {
    $("send-btn").disabled = false;
  }
});

$("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("chat-form").requestSubmit();
  }
});

// --- Documents ---

async function loadDocuments() {
  try {
    const docs = await api("/documents");
    const body = $("documents-body");
    body.innerHTML = "";
    docs.forEach((doc) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>${doc.filename}</td>
        <td class="status-${doc.status}">${doc.status}</td>
        <td>${doc.num_chunks}</td>
        <td>${doc.group_names.join(", ")}</td>
        <td><button class="delete-btn" data-id="${doc.id}">Delete</button></td>
      `;
      body.appendChild(row);
    });
    body.querySelectorAll(".delete-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api(`/documents/${btn.dataset.id}`, { method: "DELETE" });
        loadDocuments();
      });
    });

    const scope = $("chat-doc-scope");
    const previous = scope.value;
    scope.innerHTML = '<option value="">All documents (similarity search)</option>';
    docs
      .filter((doc) => doc.status === "ready")
      .forEach((doc) => {
        const opt = document.createElement("option");
        opt.value = doc.id;
        opt.textContent = doc.filename;
        scope.appendChild(opt);
      });
    if ([...scope.options].some((o) => o.value === previous)) {
      scope.value = previous;
    }
  } catch (err) {
    $("upload-status").textContent = err.message;
  }
}

$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fileInput = $("upload-file");
  const groups = $("upload-groups").value;
  if (!fileInput.files.length) return;

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  $("upload-status").textContent = "Uploading...";
  try {
    const qs = groups ? `?group_names=${encodeURIComponent(groups)}` : "";
    await api(`/documents/upload${qs}`, { method: "POST", body: formData });
    $("upload-status").textContent = "Uploaded — processing in the background.";
    fileInput.value = "";
    $("upload-groups").value = "";
    loadDocuments();
  } catch (err) {
    $("upload-status").textContent = `Error: ${err.message}`;
  }
});

// --- Users (admin only) ---

async function loadUsers() {
  try {
    const users = await api("/users");
    const body = $("users-body");
    body.innerHTML = "";
    users.forEach((u) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>${u.email}</td>
        <td>${u.full_name || ""}</td>
        <td><input type="text" class="user-groups-input" value="${u.group_names.join(", ")}" data-id="${u.id}"></td>
        <td><input type="checkbox" class="user-admin-toggle" data-id="${u.id}" ${u.is_admin ? "checked" : ""}></td>
        <td><input type="checkbox" class="user-active-toggle" data-id="${u.id}" ${u.is_active ? "checked" : ""}></td>
        <td><button class="user-save-btn" data-id="${u.id}">Save groups</button></td>
      `;
      body.appendChild(row);
    });

    body.querySelectorAll(".user-save-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const input = body.querySelector(`.user-groups-input[data-id="${btn.dataset.id}"]`);
        const group_names = input.value.split(",").map((g) => g.trim()).filter(Boolean);
        try {
          await api(`/users/${btn.dataset.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ group_names }),
          });
          $("users-status").textContent = "Saved.";
        } catch (err) {
          $("users-status").textContent = `Error: ${err.message}`;
        }
      });
    });

    body.querySelectorAll(".user-admin-toggle").forEach((toggle) => {
      toggle.addEventListener("change", async () => {
        try {
          await api(`/users/${toggle.dataset.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_admin: toggle.checked }),
          });
          $("users-status").textContent = "Saved.";
        } catch (err) {
          toggle.checked = !toggle.checked;
          $("users-status").textContent = `Error: ${err.message}`;
        }
      });
    });

    body.querySelectorAll(".user-active-toggle").forEach((toggle) => {
      toggle.addEventListener("change", async () => {
        try {
          await api(`/users/${toggle.dataset.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_active: toggle.checked }),
          });
          $("users-status").textContent = "Saved.";
        } catch (err) {
          toggle.checked = !toggle.checked;
          $("users-status").textContent = `Error: ${err.message}`;
        }
      });
    });
  } catch (err) {
    $("users-status").textContent = err.message;
  }
}

$("create-user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = $("new-user-email").value;
  const password = $("new-user-password").value;
  const full_name = $("new-user-name").value || null;
  const group_names = $("new-user-groups").value.split(",").map((g) => g.trim()).filter(Boolean);

  $("users-status").textContent = "Creating...";
  try {
    await api("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, full_name, group_names }),
    });
    $("users-status").textContent = "User created.";
    $("create-user-form").reset();
    loadUsers();
  } catch (err) {
    $("users-status").textContent = `Error: ${err.message}`;
  }
});

boot();
