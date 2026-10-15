const menu = document.querySelector('.menu-toggle');
const nav = document.querySelector('.topbar nav');
menu?.addEventListener('click', () => nav.classList.toggle('open'));
nav?.querySelectorAll('a').forEach(a => a.addEventListener('click', () => nav.classList.remove('open')));

// Demo-only outbreak indicator. Clearly labelled as simulated, not live market data.
const chaos = 68;
document.getElementById('chaos-value').textContent = `${chaos}%`;
document.getElementById('meter-fill').style.width = `${chaos}%`;
const phases = [
  ['THE DEAD ARE WALKING', 'The city is quiet. Your next move doesn’t have to be.'],
  ['SIGNAL IN THE STATIC', 'A new transmission is somewhere out there. Stay curious.'],
  ['OUTBREAK LEVEL: UNDEAD', 'The apocalypse is a mood. Keep your head on straight.']
];
let phase = 0;
setInterval(() => {
  phase = (phase + 1) % phases.length;
  document.getElementById('outbreak-status').textContent = phases[phase][0];
  document.getElementById('outbreak-message').textContent = phases[phase][1];
}, 6500);

const ca = '6cCBF9gtyp3c6VmyxUWzETk3yFmxhjvPm15cxicfpump';
document.getElementById('copy-ca').addEventListener('click', async () => {
  const feedback = document.getElementById('copy-feedback');
  try {
    await navigator.clipboard.writeText(ca);
    feedback.textContent = 'CONTRACT COPIED TO CLIPBOARD.';
  } catch {
    const input = document.createElement('textarea');
    input.value = ca; document.body.appendChild(input); input.select();
    const ok = document.execCommand('copy'); input.remove();
    feedback.textContent = ok ? 'CONTRACT COPIED TO CLIPBOARD.' : 'SELECT AND COPY THE CONTRACT ADDRESS ABOVE.';
  }
});

const start = document.getElementById('start-game');
const action = document.getElementById('game-action');
const gameMessage = document.getElementById('game-message');
const gameState = document.getElementById('game-state');
const scoreEl = document.getElementById('game-score');
const screen = document.getElementById('game-screen');
let gameTimer = null, gameStart = 0, score = 0, active = false, ready = false;
function resetGame() {
  clearTimeout(gameTimer); active = false; ready = false; score = 0;
  scoreEl.textContent = '000'; gameState.textContent = 'STANDBY';
  gameMessage.textContent = 'PRESS START TO INITIALIZE'; action.textContent = 'INITIALIZE';
  action.disabled = false; action.style.opacity = '1'; screen.style.background = '';
}
function beginGame() {
  resetGame(); active = true; gameState.textContent = 'WAIT FOR GREEN';
  gameMessage.textContent = 'WAIT FOR THE SIGNAL...'; action.textContent = 'WAIT...';
  action.disabled = true; action.style.opacity = '.45';
  const delay = 1100 + Math.random() * 3000;
  gameTimer = setTimeout(() => {
    if (!active) return;
    ready = true; gameStart = performance.now(); gameState.textContent = 'SIGNAL LIVE';
    gameMessage.textContent = 'CLICK NOW, SURVIVOR!'; action.textContent = 'STRIKE!';
    action.disabled = false; action.style.opacity = '1';
    screen.style.background = 'radial-gradient(ellipse at center,#285c28,#08120d 68%)';
  }, delay);
}
action.addEventListener('click', () => {
  if (!active) { beginGame(); return; }
  if (!ready) {
    clearTimeout(gameTimer); active = false; gameState.textContent = 'TOO EARLY';
    gameMessage.textContent = 'YOU PANICKED. TRY AGAIN.'; action.textContent = 'RETRY';
    action.disabled = false; action.style.opacity = '1'; return;
  }
  const ms = Math.round(performance.now() - gameStart);
  score = Math.max(0, 1000 - ms); scoreEl.textContent = String(score).padStart(3, '0');
  gameState.textContent = `${ms} MS`; gameMessage.textContent = ms < 280 ? 'LEGENDARY REFLEXES.' : ms < 420 ? 'NICE SURVIVAL, ZOMBIE.' : 'THE HORDE WAS CLOSE.';
  action.textContent = 'PLAY AGAIN'; active = false; ready = false; screen.style.background = '';
});
start.addEventListener('click', () => { beginGame(); document.getElementById('game-screen').scrollIntoView({behavior:'smooth',block:'center'}); });