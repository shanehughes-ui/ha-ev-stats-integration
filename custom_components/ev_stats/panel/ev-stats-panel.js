/*
 * EV Stats — the dashboard.
 *
 * A custom panel, so it is handed an authenticated `hass` by the frontend and
 * needs no token of its own. Plain DOM and no build step: one file, no
 * dependencies, readable by whoever inherits it.
 *
 * Deliberately not styled like Lovelace. This is meant to read as the car's
 * own instrument cluster — dark ground, one accent for free energy and one for
 * paid, hairline rules, tabular figures. Every colour is painted explicitly
 * rather than inherited from the theme, so it holds together on any ground.
 *
 * Two rules run through the rendering, and they are the same two that run
 * through the Python behind it:
 *
 *   - a figure that is not known prints as an em dash, never as zero;
 *   - anything that came from outside this integration is escaped before it
 *     reaches the DOM. Zone names and friendly names are user data.
 */

const WS_DASHBOARD = "ev_stats/dashboard";

/* Bucket colours. `unknown` is violet rather than a shade of grey on purpose:
   unattributed energy should catch the eye, not blend into the rules. */
const BUCKET_COLOUR = {
  home: "var(--evs-paid)",
  public_dc: "var(--evs-cool)",
  other: "var(--evs-muted)",
  unknown: "var(--evs-violet)",
};
const WORK_COLOUR = "var(--evs-free)";

const STYLES = `
:host{
  --evs-ground:#14181a; --evs-panel:#1b2123; --evs-panel-2:#20282a;
  --evs-rule:#2b3538; --evs-rule-soft:#222a2c;
  --evs-ink:#e6edec; --evs-ink-dim:#a9b8b6; --evs-muted:#7e908e;
  --evs-free:#5fd97f; --evs-free-dim:#2f6b43;
  --evs-paid:#e3a33c; --evs-paid-dim:#6d4f1c;
  --evs-cool:#5aa8c4; --evs-alert:#e2685a; --evs-violet:#9a8cd8;
  --evs-display:"Saira Semi Condensed","Arial Narrow",system-ui,sans-serif;
  --evs-body:system-ui,-apple-system,"Segoe UI",sans-serif;
  --evs-mono:ui-monospace,Menlo,Consolas,monospace;
  display:block; background:var(--evs-ground); color:var(--evs-ink);
  font-family:var(--evs-body); font-size:15px; line-height:1.55; min-height:100vh;
}
*{box-sizing:border-box}
.wrap{max-width:1180px;margin:0 auto;padding:0 22px 72px}

.mast{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;
  flex-wrap:wrap;padding:30px 0 16px;border-bottom:1px solid var(--evs-rule)}
.mast h1{font-family:var(--evs-display);font-weight:600;font-size:clamp(28px,4vw,42px);
  margin:0;line-height:1;letter-spacing:.012em}
.mast h1 em{font-style:normal;color:var(--evs-muted);font-weight:300}
.mast .meta{font-family:var(--evs-mono);font-size:11.5px;color:var(--evs-muted);
  text-align:right;line-height:1.75}
.pill{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--evs-rule);
  border-radius:999px;padding:3px 11px;font-family:var(--evs-mono);font-size:11px;
  letter-spacing:.06em;text-transform:uppercase;color:var(--evs-ink-dim)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--evs-muted)}
.dot.live{background:var(--evs-free);box-shadow:0 0 0 3px rgba(95,217,127,.22)}
.dot.warn{background:var(--evs-alert);box-shadow:0 0 0 3px rgba(226,104,90,.22)}

.answer{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(0,1fr);
  border-bottom:1px solid var(--evs-rule)}
.answer .big{padding:30px 30px 26px 0}
.kpi{font-family:var(--evs-display);font-weight:600;line-height:.86;
  font-size:clamp(58px,11vw,112px);color:var(--evs-free);font-variant-numeric:tabular-nums}
.kpi sup{font-size:.3em;font-weight:400;vertical-align:super;margin-left:.08em;
  color:var(--evs-free-dim)}
.answer .lede{margin:14px 0 0;max-width:46ch;color:var(--evs-ink-dim);font-size:15px}
.answer .lede b{color:var(--evs-ink);font-weight:600}
.side{border-left:1px solid var(--evs-rule);display:grid;grid-template-rows:1fr 1fr}
.side > div{padding:20px 0 20px 30px;display:flex;flex-direction:column;justify-content:center}
.side > div + div{border-top:1px solid var(--evs-rule-soft)}
.side .n{font-family:var(--evs-display);font-size:38px;font-weight:500;line-height:1;
  font-variant-numeric:tabular-nums}
.side .n.paid{color:var(--evs-paid)}
.lab{font-family:var(--evs-mono);font-size:10.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--evs-muted);margin-bottom:7px}
.sub{font-size:13px;color:var(--evs-muted);margin-top:5px}

section{padding:34px 0 0}
.shd{display:flex;align-items:baseline;gap:14px;margin:0 0 18px}
.shd h2{font-family:var(--evs-display);font-size:20px;font-weight:500;letter-spacing:.05em;
  text-transform:uppercase;margin:0}
.shd .rule{flex:1;height:1px;background:var(--evs-rule)}
.shd .note{font-family:var(--evs-mono);font-size:11px;color:var(--evs-muted)}

.grid{display:grid;gap:1px;background:var(--evs-rule-soft);border:1px solid var(--evs-rule-soft)}
.g2{grid-template-columns:repeat(2,minmax(0,1fr))}
.g3{grid-template-columns:repeat(3,minmax(0,1fr))}
.g4{grid-template-columns:repeat(4,minmax(0,1fr))}
.cell{background:var(--evs-panel);padding:16px 18px}
.cell .v{font-family:var(--evs-display);font-size:29px;font-weight:500;line-height:1.05;
  font-variant-numeric:tabular-nums}
.cell .v u{text-decoration:none;font-size:.44em;color:var(--evs-muted);margin-left:5px;
  font-family:var(--evs-mono)}
.cell .v.free{color:var(--evs-free)} .cell .v.paid{color:var(--evs-paid)}
.cell .v.cool{color:var(--evs-cool)} .cell .v.alert{color:var(--evs-alert)}
.cell .v.dim{color:var(--evs-ink-dim)}
.cell .cap{font-size:12.5px;color:var(--evs-muted);margin-top:6px;line-height:1.45}

.srcbar{display:flex;height:42px;border:1px solid var(--evs-rule);overflow:hidden}
.srcbar i{display:block;height:100%;font-style:normal}
.srckey{display:flex;flex-wrap:wrap;gap:18px;margin-top:12px;font-family:var(--evs-mono);
  font-size:11.5px;color:var(--evs-ink-dim)}
.srckey span{display:inline-flex;align-items:center;gap:7px}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block}

.tscroll{overflow-x:auto;border:1px solid var(--evs-rule-soft);background:var(--evs-panel)}
table{border-collapse:collapse;width:100%;font-family:var(--evs-mono);font-size:12.5px;
  font-variant-numeric:tabular-nums;white-space:nowrap}
th{text-align:left;font-weight:500;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--evs-muted);padding:10px 14px;border-bottom:1px solid var(--evs-rule);
  background:var(--evs-panel-2);position:sticky;top:0}
td{padding:8px 14px;border-bottom:1px solid var(--evs-rule-soft);color:var(--evs-ink-dim)}
tr:last-child td{border-bottom:none}
td.n{text-align:right}
td.k{color:var(--evs-ink)}
.tag{display:inline-block;padding:1px 8px;border-radius:3px;font-size:10.5px;
  border:1px solid transparent;color:var(--evs-ink-dim)}
.tag.free{color:var(--evs-free);border-color:var(--evs-free-dim)}
.tag.home{color:var(--evs-paid);border-color:var(--evs-paid-dim)}
.tag.unknown{color:var(--evs-violet);border-color:#463d6b}
.tag.public_dc{color:var(--evs-cool);border-color:#2a5566}
.flag{color:var(--evs-alert);font-size:10.5px;margin-left:6px}

figure{margin:0;background:var(--evs-panel);border:1px solid var(--evs-rule-soft);
  padding:14px 16px 10px}
figcaption{font-family:var(--evs-mono);font-size:11px;letter-spacing:.07em;
  text-transform:uppercase;color:var(--evs-muted);margin-bottom:8px;
  display:flex;justify-content:space-between;gap:12px}
figcaption em{font-style:normal;color:var(--evs-ink-dim);text-transform:none;letter-spacing:0}
svg{display:block;width:100%;height:auto;overflow:visible}

.switch{display:flex;gap:8px;margin:18px 0 0}
.switch button{font-family:var(--evs-mono);font-size:11.5px;letter-spacing:.05em;
  background:var(--evs-panel);color:var(--evs-ink-dim);border:1px solid var(--evs-rule);
  padding:5px 13px;border-radius:3px;cursor:pointer}
.switch button[aria-pressed="true"]{color:var(--evs-ink);border-color:var(--evs-free-dim);
  background:var(--evs-panel-2)}
.empty{color:var(--evs-muted);font-size:14px;padding:22px 2px}
.err{color:var(--evs-alert);font-family:var(--evs-mono);font-size:13px;padding:24px 0}

@media (max-width:820px){
  .answer{grid-template-columns:1fr}
  .answer .big{padding:26px 0 20px}
  .side{border-left:none;border-top:1px solid var(--evs-rule);grid-template-rows:auto auto}
  .side > div{padding-left:0}
  .g4{grid-template-columns:repeat(2,minmax(0,1fr))}
  .g3{grid-template-columns:repeat(2,minmax(0,1fr))}
}
`;

/* ------------------------------------------------------------- helpers -- */

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

/* An em dash, never a zero. "Not measured yet" and "measured as nothing" are
   different claims and the second one is a lie if the first is true. */
const DASH = "—";

const fmt = (value, digits = 1) =>
  value === null || value === undefined || Number.isNaN(Number(value))
    ? DASH
    : Number(value).toFixed(digits);

const truncate = (text, limit) => {
  if (!text) return DASH;
  return text.length <= limit ? text : text.slice(0, limit - 1).trimEnd() + "…";
};

const pretty = (slug) =>
  String(slug ?? "")
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());

const shortTime = (iso) => {
  if (!iso) return DASH;
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return DASH;
  return when.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
};

/* ------------------------------------------------------------- the panel -- */

class EvStatsPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._cars = null;
    this._index = 0;
    this._error = null;
    this._signature = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._cars) {
      this._load();
      return;
    }
    // Re-render only when something on the page actually changed. `hass` is
    // replaced on every state change in the whole house, and redrawing the
    // document for a light switch would make this unusable on a tablet.
    const next = this._signatureOf();
    if (next !== this._signature) {
      this._signature = next;
      this._render();
    }
  }

  connectedCallback() {
    if (this._hass && !this._cars) this._load();
  }

  async _load() {
    try {
      const result = await this._hass.callWS({ type: WS_DASHBOARD });
      this._cars = result.cars || [];
      this._error = this._cars.length ? null : "No cars are configured yet.";
    } catch (err) {
      this._error = `Could not load: ${err.message || err}`;
      this._cars = [];
    }
    this._signature = this._signatureOf();
    this._render();
  }

  get _car() {
    return this._cars ? this._cars[this._index] : null;
  }

  _signatureOf() {
    const car = this._car;
    if (!car) return "none";
    return Object.values(car.entities)
      .map((id) => this._hass.states[id]?.state ?? "")
      .join("|");
  }

  /* ------------------------------------------------------ entity access -- */
  _state(key) {
    const id = this._car?.entities?.[key];
    const state = id ? this._hass.states[id] : null;
    if (!state || state.state === "unknown" || state.state === "unavailable") {
      return null;
    }
    return state;
  }

  _num(key) {
    const state = this._state(key);
    if (!state) return null;
    const value = Number(state.state);
    return Number.isNaN(value) ? null : value;
  }

  _str(key) {
    return this._state(key)?.state ?? null;
  }

  _attr(key, name) {
    return this._state(key)?.attributes?.[name] ?? null;
  }

  /* ------------------------------------------------------------ render -- */
  _render() {
    if (this._error) {
      this.shadowRoot.innerHTML = `<style>${STYLES}</style>
        <div class="wrap"><p class="err">${esc(this._error)}</p></div>`;
      return;
    }
    if (!this._car) {
      this.shadowRoot.innerHTML = `<style>${STYLES}</style>
        <div class="wrap"><p class="empty">Loading…</p></div>`;
      return;
    }

    this.shadowRoot.innerHTML = `<style>${STYLES}</style><div class="wrap">
      ${this._mast()}
      ${this._switcher()}
      ${this._answer()}
      ${this._sources()}
      ${this._charging()}
      ${this._driving()}
      ${this._battery()}
      ${this._health()}
    </div>`;

    this.shadowRoot.querySelectorAll("[data-car]").forEach((button) => {
      button.addEventListener("click", () => {
        this._index = Number(button.dataset.car);
        this._signature = this._signatureOf();
        this._render();
      });
    });
  }

  _mast() {
    const activity = this._str("activity") || "parked";
    const charging = activity === "charging";
    const balance = this._num("balance_check");
    const off = balance !== null && Math.abs(balance) > 0.01;
    return `<div class="mast">
      <h1>${esc(this._car.title)} <em>energy</em></h1>
      <div class="meta">
        <span class="pill"><span class="dot ${charging ? "live" : off ? "warn" : ""}"></span>
          ${esc(activity)}</span><br>
        ${this._car.totals.sessions} sessions · ${this._car.totals.trips} trips
      </div>
    </div>`;
  }

  _switcher() {
    if (this._cars.length < 2) return "";
    return `<div class="switch">${this._cars
      .map(
        (car, i) =>
          `<button data-car="${i}" aria-pressed="${i === this._index}">${esc(
            car.title
          )}</button>`
      )
      .join("")}</div>`;
  }

  _answer() {
    const free = this._num("free_share");
    const saving = this._num("net_saving");
    const consumption = this._num("consumption_since_install");
    const value = this._num("free_charging_value");

    // The lede states the competence bound rather than hiding it. A
    // consumption figure computed over 40 km is not a rough answer, it is a
    // wrong one, and the page should say which it is looking at.
    const lede =
      free === null
        ? "Not enough charging recorded yet to say where the energy came from."
        : `<b>${fmt(free, 1)}%</b> of everything that went into this car cost nothing —
           charging at a free socket, plus the share of home charging the panels covered.`;

    return `<div class="answer">
      <div class="big">
        <div class="lab">Free share</div>
        <div class="kpi">${free === null ? DASH : fmt(free, 1)}<sup>%</sup></div>
        <p class="lede">${lede}</p>
      </div>
      <div class="side">
        <div>
          <div class="lab">Saved against petrol</div>
          <div class="n">${saving === null ? DASH : "$" + fmt(saving, 2)}</div>
          <div class="sub">${
            value === null
              ? "Free charging valued at what home charging costs"
              : "Free charging alone was worth $" + fmt(value, 2)
          }</div>
        </div>
        <div>
          <div class="lab">Consumption</div>
          <div class="n paid">${fmt(consumption, 2)}<span class="sub"> kWh/100km</span></div>
          <div class="sub">Measured at the wall, so it includes charging losses</div>
        </div>
      </div>
    </div>`;
  }

  _sources() {
    const detail = this._car.buckets_detail || {};
    const total = Object.values(detail).reduce((sum, b) => sum + (b.kwh || 0), 0);
    if (total <= 0) {
      return `<section>${this._head("Where the energy came from")}
        <p class="empty">Nothing metered yet.</p></section>`;
    }
    const colour = (bucket) =>
      this._car.work_buckets.includes(bucket)
        ? WORK_COLOUR
        : BUCKET_COLOUR[bucket] || "var(--evs-muted)";

    const bars = this._car.buckets
      .filter((b) => (detail[b]?.kwh || 0) > 0)
      .map(
        (b) =>
          `<i style="width:${((detail[b].kwh / total) * 100).toFixed(
            3
          )}%;background:${colour(b)}" title="${esc(pretty(b))}"></i>`
      )
      .join("");

    const key = this._car.buckets
      .filter((b) => (detail[b]?.kwh || 0) > 0)
      .map(
        (b) =>
          `<span><i class="sw" style="background:${colour(b)}"></i>${esc(
            pretty(b)
          )} ${fmt(detail[b].kwh, detail[b].kwh < 1 ? 3 : 1)} kWh</span>`
      )
      .join("");

    return `<section>
      ${this._head("Where the energy came from", fmt(total, 1) + " kWh total")}
      <div class="srcbar">${bars}</div>
      <div class="srckey">${key}</div>
    </section>`;
  }

  _charging() {
    const rows = [...(this._car.sessions || [])].reverse().slice(0, 12);
    const active = this._str("session_active") === "on";
    const cells = `<div class="grid g4">
      ${this._cell(
        "Session now",
        active ? fmt(this._num("session_energy"), 2) : DASH,
        "kWh",
        active ? "free" : "dim",
        active
          ? `Running ${fmt(this._num("session_duration"), 1)} h · routed to ${esc(
              pretty(this._attr("session_active", "routed_to") || "unknown")
            )}`
          : "Nothing charging"
      )}
      ${this._cell(
        "Charging cost",
        this._num("cost_total") === null ? DASH : "$" + fmt(this._num("cost_total"), 2),
        "",
        "paid",
        "Home charging priced at the marginal rate; solar at the feed-in it gave up"
      )}
      ${this._cell(
        "Cost per 100 km",
        this._num("cost_per_100km") === null
          ? DASH
          : "$" + fmt(this._num("cost_per_100km"), 2),
        "",
        "paid",
        "Waits for 50 km before it means anything"
      )}
      ${this._cell(
        "Home solar share",
        fmt(this._num("solar_share_home"), 1),
        "%",
        "free",
        "Of home charging, how much the panels covered"
      )}
    </div>`;

    const table = rows.length
      ? `<div class="tscroll"><table>
          <thead><tr>
            <th>Ended</th><th>Where</th><th class="n">kWh</th><th class="n">Peak</th>
            <th class="n">SoC</th><th>Why</th>
          </tr></thead><tbody>
          ${rows
            .map((s) => {
              const bucket = s.bucket || "unknown";
              const cls = this._car.work_buckets.includes(bucket)
                ? "free"
                : ["home", "unknown", "public_dc"].includes(bucket)
                ? bucket
                : "";
              const flags = (s.flags || []).length
                ? `<span class="flag" title="${esc(
                    (s.flags || []).join(", ")
                  )}">!</span>`
                : "";
              const corrected = s.corrected
                ? `<span class="flag" title="Corrected from ${esc(
                    s.original_bucket || "?"
                  )}">·</span>`
                : "";
              return `<tr>
                <td class="k">${esc(shortTime(s.end))}</td>
                <td><span class="tag ${cls}">${esc(pretty(bucket))}</span>${corrected}${flags}</td>
                <td class="n">${fmt(s.kwh, 2)}</td>
                <td class="n">${fmt(s.peak_kw, 1)}</td>
                <td class="n">${s.soc_delta === null || s.soc_delta === undefined
                  ? DASH
                  : "+" + fmt(s.soc_delta, 0)}</td>
                <td title="${esc(s.reason || "")}">${esc(
                truncate(s.reason, 52)
              )}</td>
              </tr>`;
            })
            .join("")}
          </tbody></table></div>`
      : `<p class="empty">No charging sessions recorded yet.</p>`;

    return `<section>
      ${this._head("Charging", `${rows.length} most recent`)}
      ${cells}
      <div style="height:14px"></div>
      ${table}
    </section>`;
  }

  _driving() {
    const trips = [...(this._car.trips || [])].reverse().slice(0, 10);
    const cells = `<div class="grid g4">
      ${this._cell("Distance", fmt(this._num("distance_since_install"), 0), "km", "cool",
        "Since this was set up, not the car's whole life")}
      ${this._cell("Per day", fmt(this._num("km_per_day"), 1), "km", "dim", "Average since install")}
      ${this._cell("Car's own figure", fmt(this._num("consumption_car"), 1), "kWh/100km", "dim",
        "The trip computer, which reads lower — it does not see charging losses")}
      ${this._cell("Carbon avoided", fmt(this._num("co2_avoided"), 1), "kg", "free",
        "Net of the grid carbon the charging actually incurred")}
    </div>`;

    const table = trips.length
      ? `<div class="tscroll"><table>
          <thead><tr>
            <th>Ended</th><th class="n">km</th><th class="n">min</th>
            <th class="n">km/h</th><th class="n">SoC used</th><th>Finished at</th>
          </tr></thead><tbody>
          ${trips
            .map(
              (t) => `<tr>
                <td class="k">${esc(shortTime(t.end))}</td>
                <td class="n">${fmt(t.km, 1)}</td>
                <td class="n">${fmt(t.minutes, 0)}</td>
                <td class="n">${fmt(t.avg_kmh, 0)}</td>
                <td class="n">${
                  t.soc_start === null || t.soc_end === null || t.soc_start === undefined
                    ? DASH
                    : fmt(t.soc_start - t.soc_end, 0)
                }</td>
                <td>${esc(pretty(t.ended_at) || DASH)}</td>
              </tr>`
            )
            .join("")}
          </tbody></table></div>`
      : `<p class="empty">No trips recorded yet. Trips need an engine-state entity.</p>`;

    return `<section>
      ${this._head("Driving")}
      ${cells}
      <div style="height:14px"></div>
      ${table}
    </section>`;
  }

  _battery() {
    const estimates = this._car.estimates || [];
    const pack = this._num("pack_estimate");
    const trend = this._attr("pack_estimate", "kwh_per_year");
    const corners = this._attr("tyre_lowest_cold", "corners") || {};
    const drift = this._num("tyre_drift");

    const spark = estimates.length > 1 ? this._spark(estimates.map((e) => e.implied)) : "";

    const tyres = Object.keys(corners).length
      ? `<div class="grid g4">${Object.entries(corners)
          .map(([corner, value]) =>
            this._cell(esc(corner.toUpperCase()), fmt(value, 1), "psi", "dim", "")
          )
          .join("")}</div>`
      : `<p class="empty">No tyre pressures configured.</p>`;

    return `<section>
      ${this._head("Battery and tyres")}
      <div class="grid g3">
        ${this._cell(
          "Usable pack",
          fmt(pack, 2),
          "kWh",
          "cool",
          `Mean of ${this._attr("pack_estimate", "sample_count") || 0} wide charges — one
           alone scatters too much to move this`
        )}
        ${this._cell(
          "Trend",
          trend === null || trend === undefined ? DASH : fmt(trend, 2),
          "kWh/yr",
          trend !== null && trend !== undefined && trend < -1 ? "alert" : "dim",
          "Needs measurements spread over at least six months"
        )}
        ${this._cell(
          "Tyre drift",
          fmt(drift, 2),
          "psi",
          drift !== null && drift < -2 ? "alert" : "dim",
          "Against the tyres' own 30-day history, corrected for temperature"
        )}
      </div>
      ${spark}
      <div style="height:14px"></div>
      ${tyres}
    </section>`;
  }

  _health() {
    const balance = this._num("balance_check");
    const unattributed = this._num("energy_unknown");
    const service = this._num("service_due_in");
    const secure = this._str("secure");
    const open = this._attr("secure", "open_items") || [];

    return `<section>
      ${this._head("Health")}
      <div class="grid g4">
        ${this._cell(
          "Balance check",
          fmt(balance, 3),
          "kWh",
          balance !== null && Math.abs(balance) > 0.01 ? "alert" : "free",
          "Buckets minus the master meter. Anything but zero means attribution lost energy"
        )}
        ${this._cell(
          "Unattributed",
          fmt(unattributed, 2),
          "kWh",
          "dim",
          "Energy no signal could place. Correctable from the session log"
        )}
        ${this._cell(
          "Service due",
          fmt(service, 0),
          "days",
          service !== null && service < 30 ? "alert" : "dim",
          `Whichever binds first — ${esc(this._attr("service_due_in", "binding") || "unknown")}`
        )}
        ${this._cell(
          "Security",
          secure === null ? DASH : secure === "secure" ? "Secure" : "Open",
          "",
          secure === "open" ? "alert" : "free",
          open.length ? esc(open.join(", ")) : "Read-only: this never sends a command to the car"
        )}
      </div>
    </section>`;
  }

  /* ------------------------------------------------------------- pieces -- */
  _head(title, note = "") {
    return `<div class="shd"><h2>${esc(title)}</h2><span class="rule"></span>
      ${note ? `<span class="note">${esc(note)}</span>` : ""}</div>`;
  }

  _cell(label, value, unit, tone, caption) {
    return `<div class="cell">
      <div class="lab">${label}</div>
      <div class="v ${tone}">${value}${unit ? `<u>${esc(unit)}</u>` : ""}</div>
      ${caption ? `<div class="cap">${caption}</div>` : ""}
    </div>`;
  }

  _spark(values) {
    const clean = values.filter((v) => typeof v === "number" && !Number.isNaN(v));
    if (clean.length < 2) return "";
    const min = Math.min(...clean);
    const max = Math.max(...clean);
    // A flat series must not be drawn as a dramatic wiggle. Padding the range
    // when everything agrees is what stops noise looking like a trend.
    const span = Math.max(max - min, 0.5);
    const mid = (max + min) / 2;
    const lo = mid - span / 2;
    const points = clean
      .map((v, i) => {
        const x = (i / (clean.length - 1)) * 100;
        const y = 34 - ((v - lo) / span) * 30;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
    return `<div style="height:14px"></div><figure>
      <figcaption>Pack measurements <em>${clean.length} wide charges</em></figcaption>
      <svg viewBox="0 0 100 38" preserveAspectRatio="none" style="height:78px">
        <polyline points="${points}" fill="none" stroke="var(--evs-cool)"
          stroke-width="0.8" vector-effect="non-scaling-stroke"/>
      </svg>
    </figure>`;
  }
}

customElements.define("ev-stats-panel", EvStatsPanel);
