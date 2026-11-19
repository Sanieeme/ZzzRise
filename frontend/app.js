const $ = (id) => document.getElementById(id);
let session = null;
let wakeAt = null;
let timer = null;
let alarmDone = false;
let authMode = "login";
let planMode = "duration";
let audioContext = null;
let alarmLoop = null;
const ALARM_SETTINGS_KEY = "zzrise-alarm-settings";

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toast.timeout);
  toast.timeout = setTimeout(() => element.classList.remove("show"), 3200);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch (_) {}
    throw new Error(data.detail || "Something went wrong. Please try again.");
  }
  return response.status === 204 ? null : response.json();
}

function formatTime(date) {
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function formatDuration(minutes) {
  const hrs = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hrs && mins) return `${hrs} hr ${mins} min`;
  if (hrs) return `${hrs} ${hrs === 1 ? "hour" : "hours"}`;
  return `${mins} min`;
}
function formatCountdown(milliseconds) {
  const total = Math.max(0, Math.floor(milliseconds / 1000));
  return [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60]
    .map(value => String(value).padStart(2, "0")).join(":");
}
function durationMinutes() {
  const hours = Number($("hours").value);
  if (!Number.isFinite(hours) || hours < 0.5 || hours > 14 || (hours * 2) % 1 !== 0) {
    throw new Error("Choose between 30 minutes and 14 hours, in 30-minute steps.");
  }
  return Math.round(hours * 60);
}
function getPlan() {
  const now = new Date();
  if (planMode === "duration") {
    const minutes = durationMinutes();
    return { minutes, wake: new Date(now.getTime() + minutes * 60000), method: "duration" };
  }
  const value = $("wake-time").value;
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) throw new Error("Choose a valid wake-up time.");
  const [hours, minutesPart] = value.split(":").map(Number);
  const wake = new Date(now);
  wake.setHours(hours, minutesPart, 0, 0);
  if (wake <= now) wake.setDate(wake.getDate() + 1);
  const minutes = Math.ceil((wake.getTime() - now.getTime()) / 60000);
  if (minutes < 1 || minutes > 1440) throw new Error("Choose a wake-up time within the next 24 hours.");
  return { minutes, wake, method: "wake_time" };
}
function updatePlanPreview() {
  if (session) return;
  try {
    const plan = getPlan();
    $("planned-wake").textContent = formatTime(plan.wake);
    $("preview-label").textContent = planMode === "duration" ? "ESTIMATED WAKE-UP TIME" : "YOUR WAKE-UP TIME";
    $("planned-detail").textContent = planMode === "duration"
      ? `${formatDuration(plan.minutes)} from now · ${plan.wake.toLocaleDateString([], { weekday: "short" })}`
      : `${formatDuration(plan.minutes)} from now · ${plan.wake.toLocaleDateString([], { weekday: "short" })}`;
    $("clock-status").textContent = `Your planned wake-up is ${formatTime(plan.wake)}.`;
  } catch (error) {
    $("planned-wake").textContent = "Check your selection";
    $("planned-detail").textContent = error.message;
  }
}
function setPlanMode(mode) {
  if (session) return;
  planMode = mode;
  const duration = mode === "duration";
  $("duration-mode").classList.toggle("active", duration);
  $("time-mode").classList.toggle("active", !duration);
  $("duration-mode").setAttribute("aria-selected", String(duration));
  $("time-mode").setAttribute("aria-selected", String(!duration));
  $("duration-options").hidden = !duration;
  $("time-options").hidden = duration;
  updatePlanPreview();
}
function updatePresets() {
  const current = Number($("hours").value);
  document.querySelectorAll(".preset").forEach(button => {
    button.classList.toggle("selected", Number(button.dataset.hours) === current);
  });
  updatePlanPreview();
}
function setPlanDisabled(disabled) {
  ["duration-mode", "time-mode", "hours", "wake-time"].forEach(id => { $(id).disabled = disabled; });
  document.querySelectorAll(".preset").forEach(button => { button.disabled = disabled; });
}

function setAuthMode(mode) {
  authMode = mode;
  const registering = mode === "register";
  $("name-field").hidden = !registering;
  $("name").required = registering;
  $("password").autocomplete = registering ? "new-password" : "current-password";
  $("auth-submit").textContent = registering ? "Create account" : "Log in";
  $("show-login").classList.toggle("active", !registering);
  $("show-register").classList.toggle("active", registering);
  $("show-login").setAttribute("aria-selected", String(!registering));
  $("show-register").setAttribute("aria-selected", String(registering));
  $("auth-error").textContent = "";
}
async function showApp(user) {
  $("auth-view").hidden = true;
  $("app-view").hidden = false;
  $("account-bar").hidden = false;
  $("welcome-user").textContent = `Hi, ${user.name.split(" ")[0]}`;
  await refreshSessions();
  updatePlanPreview();
}
async function checkLogin() {
  try { const user = await request("/api/auth/me"); await showApp(user); }
  catch (_) { $("auth-view").hidden = false; $("app-view").hidden = true; $("account-bar").hidden = true; }
}

$("show-login").addEventListener("click", () => setAuthMode("login"));
$("show-register").addEventListener("click", () => setAuthMode("register"));
$("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = { email: $("email").value.trim(), password: $("password").value };
  if (authMode === "register") payload.name = $("name").value.trim();
  const endpoint = authMode === "register" ? "/api/auth/register" : "/api/auth/login";
  $("auth-submit").disabled = true;
  $("auth-error").textContent = "";
  try {
    const user = await request(endpoint, { method: "POST", body: JSON.stringify(payload) });
    $("auth-form").reset();
    await showApp(user);
  } catch (error) { $("auth-error").textContent = error.message; }
  finally { $("auth-submit").disabled = false; }
});
$("logout").addEventListener("click", async () => {
  try { await request("/api/auth/logout", { method: "POST" }); } catch (_) {}
  clearInterval(timer); clearInterval(alarmLoop);
  session = null; wakeAt = null; alarmDone = false;
  $("auth-view").hidden = false; $("app-view").hidden = true; $("account-bar").hidden = true;
  setAuthMode("login");
  toast("You have been logged out.");
});

async function recordEvent(type, details = "") {
  if (!session) return;
  try {
    await request("/api/events", { method: "POST", body: JSON.stringify({ session_id: session.id, event_type: type, recorded_at: new Date().toISOString(), details }) });
  } catch (error) { console.warn("Could not record event", error); }
}
function getAlarmSettings() {
  const defaults = { sound: "gentle", volume: 70 };
  try { return { ...defaults, ...(JSON.parse(localStorage.getItem(ALARM_SETTINGS_KEY) || "{}")) }; }
  catch (_) { return defaults; }
}
function saveAlarmSettings() {
  const settings = { sound: $("alarm-sound").value, volume: Number($("alarm-volume").value) };
  localStorage.setItem(ALARM_SETTINGS_KEY, JSON.stringify(settings));
  $("volume-value").textContent = `${settings.volume}%`;
}
async function prepareAudio() {
  try {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (Audio && !audioContext) audioContext = new Audio();
    if (audioContext && audioContext.state === "suspended") await audioContext.resume();
    return Boolean(audioContext && audioContext.state === "running");
  } catch (error) { console.warn("Audio could not be prepared", error); return false; }
}
function playTone(frequency, offset, duration, waveform, volume, context) {
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.type = waveform;
  oscillator.frequency.setValueAtTime(frequency, context.currentTime + offset);
  gain.gain.setValueAtTime(0.0001, context.currentTime + offset);
  gain.gain.exponentialRampToValueAtTime(Math.max(0.001, volume), context.currentTime + offset + 0.025);
  gain.gain.setValueAtTime(Math.max(0.001, volume), context.currentTime + offset + Math.max(0.04, duration - 0.08));
  gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + offset + duration);
  oscillator.connect(gain); gain.connect(context.destination);
  oscillator.start(context.currentTime + offset);
  oscillator.stop(context.currentTime + offset + duration + 0.02);
}
function playAlarmPattern() {
  if (!audioContext || audioContext.state !== "running") return;
  const settings = getAlarmSettings();
  const volume = Math.max(0.01, settings.volume / 100 * 0.22);
  const context = audioContext;
  if (settings.sound === "classic") {
    playTone(880, 0, 0.32, "square", volume, context);
    playTone(660, 0.42, 0.32, "square", volume, context);
    playTone(880, 0.9, 0.32, "square", volume, context);
  } else if (settings.sound === "digital") {
    playTone(740, 0, 0.16, "triangle", volume, context);
    playTone(990, 0.2, 0.16, "triangle", volume, context);
    playTone(740, 0.48, 0.16, "triangle", volume, context);
    playTone(990, 0.68, 0.16, "triangle", volume, context);
  } else {
    playTone(784, 0, 0.65, "sine", volume, context);
    playTone(988, 0.42, 0.7, "sine", volume * 0.9, context);
    playTone(1175, 0.9, 0.8, "sine", volume * 0.8, context);
  }
}
async function testAlarmSound() {
  const ready = await prepareAudio();
  if (!ready) { toast("Your browser blocked sound. Try again after interacting with the page."); return; }
  playAlarmPattern();
  toast("Playing your selected alarm sound.");
}
async function enableNotifications() {
  if (!("Notification" in window)) { toast("This browser does not support notifications."); return; }
  try {
    const permission = await Notification.requestPermission();
    toast(permission === "granted" ? "Wake-up notifications enabled." : "Notifications were not enabled. Check your browser settings.");
  } catch (_) { toast("Could not enable notifications in this browser."); }
}
function loadAlarmSettings() {
  const settings = getAlarmSettings();
  $("alarm-sound").value = ["gentle", "classic", "digital"].includes(settings.sound) ? settings.sound : "gentle";
  $("alarm-volume").value = Math.min(100, Math.max(10, Number(settings.volume) || 70));
  $("volume-value").textContent = `${$("alarm-volume").value}%`;
}

async function start() {
  if (session) return;
  let plan;
  try { plan = getPlan(); }
  catch (error) { toast(error.message); return; }
  await prepareAudio();
  const started = new Date();
  // Recalculate from the actual start click to avoid a stale preview.
  try {
    if (planMode === "duration") plan = getPlan();
    else {
      const value = $("wake-time").value;
      const [hh, mm] = value.split(":").map(Number);
      const target = new Date(started);
      target.setHours(hh, mm, 0, 0);
      if (target <= started) target.setDate(target.getDate() + 1);
      plan = { wake: target, minutes: Math.ceil((target - started) / 60000), method: "wake_time" };
    }
  } catch (error) { toast(error.message); return; }
  if (plan.minutes < 1 || plan.minutes > 1440) { toast("Choose a wake-up time within the next 24 hours."); return; }
  wakeAt = plan.wake;
  try {
    session = await request("/api/sessions", { method: "POST", body: JSON.stringify({ started_at: started.toISOString(), target_minutes: plan.minutes, start_method: plan.method }) });
    alarmDone = false;
    localStorage.setItem("zzrise-active-session", JSON.stringify({ id: session.id, wakeAt: wakeAt.toISOString() }));
    renderActive();
    clearInterval(timer);
    timer = setInterval(tick, 1000);
    tick();
    await recordEvent("sleep_session_started", `target_minutes=${plan.minutes}`);
    toast(`Your session has started. Wake-up is set for ${formatTime(wakeAt)}.`);
  } catch (error) { session = null; wakeAt = null; toast(error.message); }
}
function renderActive() {
  setPlanDisabled(true);
  $("start").hidden = true;
  $("stop").hidden = false;
  $("stop").textContent = alarmDone ? "Stop alarm & save session" : "End session";
  $("status").textContent = "SESSION IN PROGRESS";
  $("monitor").textContent = "In progress";
  $("session-title").textContent = "Rest easy";
  $("clock-status").textContent = `Wake-up at ${formatTime(wakeAt)}`;
  $("planned-wake").textContent = formatTime(wakeAt);
  $("preview-label").textContent = "YOUR WAKE-UP TIME";
  $("planned-detail").textContent = `${formatDuration(session.target_minutes)} after your session started`;
  $("message").textContent = `Your session is underway. Your wake-up reminder is set for ${formatTime(wakeAt)}.`;
}
function tick() {
  if (!wakeAt || alarmDone) return;
  const remaining = wakeAt.getTime() - Date.now();
  $("countdown").textContent = formatCountdown(remaining);
  if (remaining <= 0) ringAlarm();
}
function ringAlarm() {
  if (alarmDone) return;
  alarmDone = true;
  clearInterval(timer);
  $("status").textContent = "TIME TO WAKE UP";
  $("countdown").textContent = "00:00:00";
  $("clock-status").textContent = "Your planned wake-up time has arrived.";
  $("message").textContent = "Time to wake up. Stop the alarm to save your sleep session.";
  $("stop").textContent = "Stop alarm & save session";
  $("session-title").textContent = "Good morning!";
  toast("Wake-up time!");
  playAlarmPattern();
  clearInterval(alarmLoop);
  alarmLoop = setInterval(playAlarmPattern, 2400);
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification("ZzzRise — Time to wake up", { body: "Your planned rest time is complete." }); } catch (_) {}
  }
}
async function stop() {
  if (!session) return;
  try {
    await request(`/api/sessions/${session.id}/end`, { method: "PATCH", body: JSON.stringify({ ended_at: new Date().toISOString() }) });
    await recordEvent("sleep_session_ended");
    clearInterval(timer); clearInterval(alarmLoop);
    session = null; wakeAt = null; alarmDone = false;
    localStorage.removeItem("zzrise-active-session");
    setPlanDisabled(false);
    $("start").hidden = false; $("stop").hidden = true;
    $("status").textContent = "SESSION SAVED"; $("monitor").textContent = "Not started";
    $("session-title").textContent = "Ready when you are";
    $("countdown").textContent = "--:--:--";
    $("clock-status").textContent = "Choose a plan for your next rest.";
    $("message").textContent = "Your session has been saved. You can start another whenever you are ready.";
    updatePlanPreview();
    await refreshSessions();
    toast("Your sleep session has been saved.");
  } catch (error) { toast(error.message); }
}

function localDateKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
function completedRows(rows) {
  return rows.filter(row => row.ended_at && row.started_at && new Date(row.ended_at) >= new Date(row.started_at));
}
function renderWeeklyChart(completed) {
  const container = $("weekly-chart");
  container.replaceChildren();
  if (!completed.length) {
    const empty = document.createElement("p");
    empty.className = "chart-empty";
    empty.textContent = "Your chart will appear after your first completed session.";
    container.append(empty);
    return;
  }
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = [];
  for (let offset = 6; offset >= 0; offset--) {
    const date = new Date(today);
    date.setDate(today.getDate() - offset);
    days.push({ date, key: localDateKey(date), minutes: 0 });
  }
  const byDate = new Map(days.map(day => [day.key, day]));
  completed.forEach(row => {
    const start = new Date(row.started_at);
    const end = new Date(row.ended_at);
    const day = byDate.get(localDateKey(start));
    if (day) day.minutes += Math.max(0, (end - start) / 60000);
  });
  const maxHours = Math.max(8, ...days.map(day => day.minutes / 60));
  days.forEach(day => {
    const hours = day.minutes / 60;
    const column = document.createElement("div");
    column.className = "bar-column";
    const value = document.createElement("span");
    value.className = "bar-value";
    value.textContent = hours ? `${hours.toFixed(1)}h` : "–";
    const track = document.createElement("div");
    track.className = "bar-track";
    const bar = document.createElement("div");
    bar.className = "bar-fill";
    bar.style.height = `${hours ? Math.max(5, hours / maxHours * 100) : 0}%`;
    bar.title = `${day.date.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" })}: ${formatDuration(Math.round(day.minutes))}`;
    track.append(bar);
    const label = document.createElement("span");
    label.className = "bar-label";
    label.textContent = day.date.toLocaleDateString([], { weekday: "short" });
    column.append(value, track, label);
    container.append(column);
  });
}
function renderGoalChart(completed) {
  const met = completed.filter(row => {
    const actual = (new Date(row.ended_at) - new Date(row.started_at)) / 60000;
    return actual >= Number(row.target_minutes || 480);
  }).length;
  const rate = completed.length ? Math.round(met / completed.length * 100) : 0;
  $("goal-donut").style.setProperty("--progress", `${rate}%`);
  $("goal-donut").setAttribute("aria-label", `${rate} percent of sleep goals reached`);
  $("goal-donut-value").textContent = completed.length ? `${rate}%` : "—";
}
function renderHistory(rows) {
  const completed = completedRows(rows);
  $("session-count").textContent = completed.length;
  $("history-total").textContent = completed.length;
  if (completed.length) {
    const durations = completed.map(row => (new Date(row.ended_at) - new Date(row.started_at)) / 60000);
    const average = durations.reduce((sum, value) => sum + value, 0) / durations.length;
    const met = completed.filter(row => ((new Date(row.ended_at) - new Date(row.started_at)) / 60000) >= Number(row.target_minutes || 480)).length;
    $("average-sleep").textContent = formatDuration(Math.round(average));
    $("goal-rate").textContent = `${Math.round(met / completed.length * 100)}%`;
  } else {
    $("average-sleep").textContent = "—";
    $("goal-rate").textContent = "—";
  }
  const weekStart = new Date();
  weekStart.setHours(0, 0, 0, 0);
  weekStart.setDate(weekStart.getDate() - 6);
  const weeklyMinutes = completed.reduce((sum, row) => {
    const start = new Date(row.started_at);
    return start >= weekStart ? sum + Math.max(0, (new Date(row.ended_at) - start) / 60000) : sum;
  }, 0);
  $("weekly-total").textContent = completed.length ? formatDuration(Math.round(weeklyMinutes)) : "—";
  renderWeeklyChart(completed);
  renderGoalChart(completed);

  const history = $("history");
  history.replaceChildren();
  if (!completed.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = '<span aria-hidden="true">☾</span><strong>Your sleep history will appear here</strong><p>Complete a session to start seeing your progress.</p>';
    history.append(empty);
    return;
  }
  completed.slice(0, 10).forEach(row => {
    const startAt = new Date(row.started_at);
    const endAt = new Date(row.ended_at);
    const minutes = Math.max(0, Math.round((endAt - startAt) / 60000));
    const met = minutes >= Number(row.target_minutes || 480);
    const item = document.createElement("div");
    item.className = "history-row";
    const details = document.createElement("div");
    const title = document.createElement("b");
    title.textContent = `${startAt.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} · ${formatDuration(minutes)}`;
    const subtitle = document.createElement("small");
    subtitle.textContent = `${formatTime(startAt)} – ${formatTime(endAt)} · Goal ${formatDuration(row.target_minutes)}`;
    details.append(title, subtitle);
    const tag = document.createElement("span");
    tag.className = `history-tag ${met ? "achieved" : ""}`;
    tag.textContent = met ? "Goal reached" : "Completed";
    item.append(details, tag);
    history.append(item);
  });
}
async function refreshSessions() {
  try {
    const rows = await request("/api/sessions");
    renderHistory(rows);
    const active = rows.find(row => row.status === "active" && !row.ended_at);
    if (active && (!session || session.id !== active.id)) restoreSession(active);
  } catch (error) { toast(error.message); }
}
function restoreSession(active) {
  session = active;
  let stored = null;
  try { stored = JSON.parse(localStorage.getItem("zzrise-active-session") || "null"); } catch (_) {}
  const storedWake = stored && stored.id === active.id ? new Date(stored.wakeAt) : null;
  wakeAt = storedWake && !Number.isNaN(storedWake.getTime())
    ? storedWake
    : new Date(new Date(active.started_at).getTime() + active.target_minutes * 60000);
  localStorage.setItem("zzrise-active-session", JSON.stringify({ id: active.id, wakeAt: wakeAt.toISOString() }));
  alarmDone = false;
  renderActive();
  clearInterval(timer);
  timer = setInterval(tick, 1000);
  tick();
}

$("duration-mode").addEventListener("click", () => setPlanMode("duration"));
$("time-mode").addEventListener("click", () => setPlanMode("time"));
$("hours").addEventListener("input", updatePresets);
$("wake-time").addEventListener("input", updatePlanPreview);
document.querySelectorAll(".preset").forEach(button => button.addEventListener("click", () => {
  $("hours").value = button.dataset.hours;
  updatePresets();
}));
$("alarm-sound").addEventListener("change", saveAlarmSettings);
$("alarm-volume").addEventListener("input", saveAlarmSettings);
$("test-alarm").addEventListener("click", testAlarmSound);
$("enable-notifications").addEventListener("click", enableNotifications);
$("start").addEventListener("click", start);
$("stop").addEventListener("click", stop);
document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
loadAlarmSettings();
updatePresets();
checkLogin();
