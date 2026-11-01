/* Ogive viewer: an MCP App (ui://ogive/viewer.html). Draws only what Ogive sends, which comes from
 * OpenRocket's model of the file. Talks to the host with JSON-RPC 2.0 over postMessage (MCP Apps
 * 2026-01-26): ui/initialize -> ui/notifications/initialized -> ui/notifications/tool-result. */
(function () {
  "use strict";

  var PROTOCOL = "2026-01-26";
  var VERSION = "__OGIVE_VERSION__";
  var app = document.getElementById("app");
  var T = window.THREE;

  // ---------- bridge ----------
  var nextId = 1;
  var pending = {};

  function post(message) {
    message.jsonrpc = "2.0";
    window.parent.postMessage(message, "*");
  }
  function request(method, params) {
    var id = nextId++;
    post({ id: id, method: method, params: params });
    return new Promise(function (resolve, reject) {
      pending[id] = { resolve: resolve, reject: reject };
    });
  }
  function notify(method, params) {
    post({ method: method, params: params || {} });
  }

  window.addEventListener("message", function (event) {
    var m = event.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.id !== undefined && pending[m.id] && (m.result !== undefined || m.error)) {
      var p = pending[m.id];
      delete pending[m.id];
      if (m.error) p.reject(m.error); else p.resolve(m.result);
      return;
    }
    if (m.method === "ui/notifications/tool-result") onResult(m.params || {});
    else if (m.method === "ui/notifications/host-context-changed") applyContext(m.params || {});
    else if (m.method === "ui/resource-teardown") { teardown(); post({ id: m.id, result: {} }); }
    else if (m.method === "ping" && m.id !== undefined) post({ id: m.id, result: {} });
  });

  // Each mode's design height. The host may fix the height or cap it (containerDimensions); the
  // page then fits inside, so the controls are never below the frame.
  var HEIGHTS = { design: 560, versions: 640, flight: 520 };
  var limits = {};
  function fitHeight(design) {
    if (limits.height) return limits.height;
    return limits.maxHeight ? Math.min(design, limits.maxHeight) : design;
  }

  function applyContext(ctx) {
    if (ctx.containerDimensions) {
      limits = ctx.containerDimensions;
      if (view && HEIGHTS[app.className]) {
        app.style.height = fitHeight(HEIGHTS[app.className]) + "px";
        view.resize();
        reportSize();
      }
    }
    if (ctx.theme) document.documentElement.setAttribute("data-theme", ctx.theme);
    var vars = ctx.styles && ctx.styles.variables;
    if (vars && vars["--font-sans"]) {
      document.documentElement.style.setProperty("--font-sans", vars["--font-sans"]);
    }
    if (view) view.requestRender();
  }

  // The app's own box, not the document's: the document is never shorter than the iframe the
  // host already gave us, so it could grow the frame but never shrink it.
  function reportSize() {
    notify("ui/notifications/size-changed", {
      width: Math.ceil(app.getBoundingClientRect().width),
      height: Math.ceil(app.getBoundingClientRect().height),
    });
  }

  function onResult(result) {
    var data = result.structuredContent || result.structured_content;
    if (!data && result.content) {
      for (var i = 0; i < result.content.length; i++) {
        var c = result.content[i];
        if (c.type === "text") {
          try { data = JSON.parse(c.text); break; } catch (e) { /* not JSON */ }
        }
      }
    }
    if (!data) { renderFallback(result, "This result has no viewer data."); return; }
    try {
      render(data, result);
    } catch (error) {
      renderFallback(result, "The viewer couldn't draw this: " +
        String(error.message).replace(/\.$/, "") + ".");
    }
    reportSize();
  }

  // Anything the page can't draw (a mode it doesn't know, no WebGL, an error) still shows the
  // tool's own numbers and the side view it attached: never just an error.
  function renderFallback(result, reason) {
    teardown();
    app.innerHTML = "";
    app.className = "fallback";
    app.style.height = "";
    var box = el("div", { id: "fallback" });
    box.appendChild(el("p", { class: "muted" }, reason + " Showing the numbers and the side view instead."));
    var record = null;
    (result.content || []).forEach(function (c) {
      if (c.type === "image" && c.data) {
        box.appendChild(el("img", { alt: "Side view of the design, drawn by Ogive from OpenRocket's model",
          src: "data:" + (c.mimeType || c.mime_type || "image/png") + ";base64," + c.data }));
      } else if (c.type === "text" && !record) {
        try { record = JSON.parse(c.text); } catch (e) { box.appendChild(el("pre", {}, c.text)); }
      }
    });
    if (record) box.appendChild(recordTables(record));
    app.appendChild(box);
    reportSize();
  }

  function recordTables(record) {
    var wrap = el("div");
    var simple = el("table");
    wrap.appendChild(simple);
    function cell(v) {
      if (v === null || v === undefined) return "";
      if (Array.isArray(v)) return v.map(cell).join("; ");
      if (typeof v === "number") return String(Number(v.toPrecision(6)));
      if (typeof v === "object") return JSON.stringify(v);
      return String(v);
    }
    function row(table, key, value) {
      var tr = el("tr");
      tr.appendChild(el("th", {}, key));
      tr.appendChild(el("td", {}, cell(value)));
      table.appendChild(tr);
    }
    Object.keys(record).forEach(function (key) {
      var v = record[key];
      if (Array.isArray(v) && v.length && v[0] !== null && typeof v[0] === "object") {
        var keys = Object.keys(v[0]).filter(function (k) { return typeof v[0][k] !== "object" || v[0][k] === null ||
          Array.isArray(v[0][k]); });
        var table = el("table");
        var head = el("tr");
        keys.forEach(function (k) { head.appendChild(el("th", {}, k)); });
        table.appendChild(head);
        v.forEach(function (item) {
          var tr = el("tr");
          keys.forEach(function (k) { tr.appendChild(el("td", {}, cell(item[k]))); });
          table.appendChild(tr);
        });
        wrap.appendChild(el("h2", {}, key));
        wrap.appendChild(table);
      } else if (v !== null && typeof v === "object" && !Array.isArray(v)) {
        Object.keys(v).forEach(function (k) { if (typeof v[k] !== "object") row(simple, key + "." + k, v[k]); });
      } else {
        row(simple, key, v);
      }
    });
    return wrap;
  }

  function showMessage(text) {
    app.innerHTML = "";
    var div = document.createElement("div");
    div.id = "message";
    div.textContent = text;
    app.appendChild(div);
  }

  // ---------- helpers ----------
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function mm(m) { return (m * 1000).toFixed(m < 0.1 ? 1 : 0); }
  function grams(kg) { return (kg * 1000).toFixed(1) + " g"; }
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // ---------- 3D view ----------
  var view = null;

  function View(container) {
    var self = this;
    this.container = container;
    this.width = container.clientWidth || 800;
    this.height = container.clientHeight || 560;
    this.renderer = new T.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(1); // 3.5 GB laptops: keep the framebuffer small
    this.renderer.setSize(this.width, this.height);
    container.appendChild(this.renderer.domElement);
    this.scene = new T.Scene();
    this.camera = new T.PerspectiveCamera(35, this.width / this.height, 0.001, 50000);
    this.scene.add(new T.HemisphereLight(0xffffff, 0x8a8f99, 1.15));
    var sun = new T.DirectionalLight(0xffffff, 1.4);
    sun.position.set(1, 2, 1.5);
    this.scene.add(sun);
    this.root = new T.Group();
    this.scene.add(this.root);
    this.tags = [];
    this.target = new T.Vector3();
    this.spherical = { r: 1, theta: 0.6, phi: 1.2 };
    this.frameRequested = false;
    this.onFrame = null;

    var dragging = null;
    var el2 = this.renderer.domElement;
    el2.addEventListener("pointerdown", function (e) {
      dragging = { x: e.clientX, y: e.clientY, button: e.button };
      el2.setPointerCapture(e.pointerId);
    });
    el2.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      var dx = e.clientX - dragging.x, dy = e.clientY - dragging.y;
      dragging.x = e.clientX; dragging.y = e.clientY;
      if (dragging.button === 2 || e.shiftKey) {
        var pan = self.spherical.r * 0.0015;
        var right = new T.Vector3().setFromMatrixColumn(self.camera.matrix, 0);
        var up = new T.Vector3().setFromMatrixColumn(self.camera.matrix, 1);
        self.target.addScaledVector(right, -dx * pan).addScaledVector(up, dy * pan);
      } else {
        self.spherical.theta -= dx * 0.008;
        self.spherical.phi = Math.min(Math.PI - 0.05, Math.max(0.05, self.spherical.phi - dy * 0.008));
      }
      self.requestRender();
    });
    el2.addEventListener("pointerup", function () { dragging = null; });
    el2.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    el2.addEventListener("wheel", function (e) {
      e.preventDefault();
      self.spherical.r *= Math.exp(e.deltaY * 0.0012);
      self.requestRender();
    }, { passive: false });
    window.addEventListener("resize", function () { self.resize(); });
  }

  View.prototype.resize = function () {
    this.width = this.container.clientWidth || this.width;
    this.height = this.container.clientHeight || this.height;
    this.camera.aspect = this.width / this.height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(this.width, this.height);
    this.requestRender();
  };

  View.prototype.requestRender = function () {
    var self = this;
    if (this.frameRequested) return;
    this.frameRequested = true;
    requestAnimationFrame(function () {
      self.frameRequested = false;
      self.draw();
    });
  };

  View.prototype.draw = function () {
    var s = this.spherical;
    this.camera.position.set(
      this.target.x + s.r * Math.sin(s.phi) * Math.sin(s.theta),
      this.target.y + s.r * Math.cos(s.phi),
      this.target.z + s.r * Math.sin(s.phi) * Math.cos(s.theta)
    );
    this.camera.lookAt(this.target);
    if (this.onFrame) this.onFrame();
    this.renderer.render(this.scene, this.camera);
    this.placeTags();
  };

  // Aim at a box from direction (theta, phi) and back off until it fits: across the width, and
  // up/down inside the band the panels leave free (about 75 % of the height). The box's depth
  // along the view is added, so the camera never starts inside the rocket (tail view).
  View.prototype.frame = function (box, theta, phi) {
    var s = this.spherical;
    s.theta = theta;
    s.phi = phi;
    box.getCenter(this.target);
    var dir = new T.Vector3(Math.sin(phi) * Math.sin(theta), Math.cos(phi), Math.sin(phi) * Math.cos(theta));
    var right = new T.Vector3(0, 1, 0).cross(dir).normalize();
    var up = dir.clone().cross(right).normalize();
    var half = box.getSize(new T.Vector3()).multiplyScalar(0.5);
    var extent = function (a) {
      return Math.abs(a.x) * half.x + Math.abs(a.y) * half.y + Math.abs(a.z) * half.z;
    };
    var halfV = Math.tan((this.camera.fov * Math.PI) / 360);
    var fit = Math.max(extent(right) / (halfV * this.camera.aspect), extent(up) / (halfV * 0.75));
    s.r = fit * 1.05 + extent(dir);
    this.camera.near = s.r / 1000;
    this.camera.far = s.r * 100;
    this.camera.updateProjectionMatrix();
    this.requestRender();
  };

  // A label pinned to a 3D point, `dy` pixels above (negative) or below it on screen, so it never
  // covers the point it names.
  View.prototype.tag = function (text, cls, position, dy) {
    var div = el("div", { class: "tag " + cls }, text);
    this.container.appendChild(div);
    var entry = { div: div, position: position, dy: dy || 0, visible: true };
    this.tags.push(entry);
    return entry;
  };

  // Labels are placed in order; one that would overlap a label already placed moves a row
  // further from its point (up if it sits above, down if below).
  View.prototype.placeTags = function () {
    var v = new T.Vector3();
    var placed = [];
    for (var i = 0; i < this.tags.length; i++) {
      var t = this.tags[i];
      v.copy(t.position).project(this.camera);
      var show = t.visible && v.z < 1;
      t.div.style.display = show ? "block" : "none";
      if (!show) continue;
      var x = ((v.x + 1) / 2) * this.width;
      var y = ((1 - v.y) / 2) * this.height + t.dy;
      var w = t.div.offsetWidth, h = t.div.offsetHeight;
      var step = (t.dy < 0 ? -1 : 1) * (h + 2);
      for (var tries = 0; tries < 12 && overlaps(placed, x, y, w, h); tries++) y += step;
      placed.push({ x: x, y: y, w: w, h: h });
      t.div.style.left = x + "px";
      t.div.style.top = y + "px";
    }
  };

  function overlaps(placed, x, y, w, h) {
    for (var i = 0; i < placed.length; i++) {
      var p = placed[i];
      if (Math.abs(p.x - x) < (p.w + w) / 2 + 2 && Math.abs(p.y - y) < (p.h + h) / 2 + 1) return true;
    }
    return false;
  }

  View.prototype.dispose = function () {
    this.scene.traverse(function (o) {
      if (o.geometry) o.geometry.dispose();
      if (o.material) o.material.dispose();
    });
    this.renderer.dispose();
  };

  function teardown() {
    if (view) view.dispose();
    view = null;
  }

  // ---------- rocket meshes (all geometry from OpenRocket via Ogive) ----------
  var COLOURS = {
    body: 0xd9d4c5, fin: 0xcfc8b5, part: 0xf7f7f2, motor: 0x6b7280, assumed: 0xf5b041,
  };

  function massColour(mass, maxMass) {
    var t = Math.sqrt(Math.max(0, Math.min(1, mass / maxMass)));
    var light = new T.Color(0xe3e8f2), dark = new T.Color(0x14181f);
    return light.lerp(dark, t);
  }

  function material(colour, opts) {
    return new T.MeshStandardMaterial(Object.assign({
      color: colour, roughness: 0.75, metalness: 0.05, side: T.DoubleSide,
    }, opts || {}));
  }

  function alongX(mesh) {
    mesh.rotation.z = -Math.PI / 2; // three.js lathes and cylinders run along Y; rockets along X
    return mesh;
  }

  function buildRocket(model, assumed) {
    var group = new T.Group();
    var meshes = [];
    var isAssumed = function (name) { return assumed.indexOf(name) >= 0; };
    model.bodies.forEach(function (b) {
      var pts = b.profile.map(function (p) { return new T.Vector2(Math.max(p[1], 1e-5), p[0]); });
      var mesh = alongX(new T.Mesh(new T.LatheGeometry(pts, 64), material(COLOURS.body)));
      mesh.userData = { role: "body", name: b.name, mass: b.mass, assumed: isAssumed(b.name) };
      group.add(mesh); meshes.push(mesh);
    });
    model.fin_sets.forEach(function (f) {
      var shape = new T.Shape(f.points.map(function (p) { return new T.Vector2(p[0], p[1]); }));
      var geometry = new T.ExtrudeGeometry(shape, { depth: f.thickness, bevelEnabled: false });
      geometry.translate(0, 0, -f.thickness / 2);
      f.instances.forEach(function (inst) {
        var holder = new T.Group();
        var mesh = new T.Mesh(geometry, material(COLOURS.fin));
        mesh.position.set(inst[0], inst[1], 0);
        holder.add(mesh);
        holder.rotation.x = inst[2]; // OpenRocket roll angle: 0 = +Y, positive towards +Z
        holder.userData = { role: "fin", baseRoot: inst[1] };
        mesh.userData = { role: "fin", name: f.name, mass: f.mass / f.instances.length,
          assumed: isAssumed(f.name), holder: holder };
        group.add(holder); meshes.push(mesh);
      });
    });
    model.parts.forEach(function (p) {
      var mesh = alongX(new T.Mesh(new T.CylinderGeometry(p.radius, p.radius, p.length, 28),
        material(COLOURS.part)));
      mesh.position.set(p.x0 + p.length / 2, p.y, p.z);
      mesh.userData = { role: "part", name: p.name, mass: p.mass, assumed: isAssumed(p.name),
        home: mesh.position.clone(), x0: p.x0, x1: p.x0 + p.length };
      group.add(mesh); meshes.push(mesh);
    });
    model.motors.forEach(function (m) {
      var mesh = alongX(new T.Mesh(new T.CylinderGeometry(m.radius, m.radius, m.length, 32),
        material(COLOURS.motor)));
      mesh.position.set(m.x0 + m.length / 2, 0, 0);
      mesh.userData = { role: "motor", name: m.name, mass: m.mass, assumed: isAssumed(m.name),
        home: mesh.position.clone(), x0: m.x0, x1: m.x0 + m.length };
      group.add(mesh); meshes.push(mesh);
    });
    // Exploded lanes: nose to tail, each part takes the first lane where it overlaps nothing.
    var laneEnds = [];
    meshes.filter(function (m) { return m.userData.home; })
      .sort(function (a, b) { return a.userData.x0 - b.userData.x0; })
      .forEach(function (m) {
        var lane = 0;
        while (lane < laneEnds.length && laneEnds[lane] > m.userData.x0 - 0.01) lane++;
        laneEnds[lane] = m.userData.x1;
        m.userData.shelf = lane;
      });
    return { group: group, meshes: meshes };
  }

  function styleRocket(rocket, options, maxRadius) {
    var maxMass = 1e-9;
    rocket.meshes.forEach(function (m) { maxMass = Math.max(maxMass, m.userData.mass); });
    rocket.meshes.forEach(function (m) {
      var u = m.userData, mat = m.material;
      var colour = options.byMass ? massColour(u.mass, maxMass)
        : new T.Color(COLOURS[u.role] !== undefined ? COLOURS[u.role] : COLOURS.part);
      if (u.assumed) colour = new T.Color(COLOURS.assumed);
      mat.color.copy(colour);
      mat.emissive = new T.Color(u.assumed ? 0x6b3a00 : 0x000000);
      var seeThrough = u.role === "body" && (options.xray || options.exploded);
      mat.transparent = seeThrough;
      mat.opacity = seeThrough ? 0.22 : 1;
      mat.depthWrite = !seeThrough;
      if (u.home) {
        // Exploded: parts drop below the rocket, one lane per overlap.
        m.position.copy(u.home);
        if (options.exploded) m.position.y -= maxRadius * (3 + 3 * u.shelf);
      }
      if (u.holder) {
        u.holder.children[0].position.y = u.holder.userData.baseRoot +
          (options.exploded ? maxRadius * 1.4 : 0);
      }
    });
  }

  function marker(colour, radius, filled) {
    var mesh = new T.Mesh(new T.SphereGeometry(radius, 20, 14),
      new T.MeshBasicMaterial({ color: colour, depthTest: false, transparent: true,
        opacity: filled ? 0.95 : 0.55 }));
    mesh.renderOrder = 10;
    return mesh;
  }

  // ---------- design mode ----------
  // The modes this page draws, by structuredContent "kind". Ogive's server sends exactly these
  // (tests/test_viewer.py checks both sides); anything else falls back to numbers + side view.
  var MODES = { design: renderDesign, versions: renderVersions, flight: renderFlight };

  function render(data, result) {
    var draw = Object.prototype.hasOwnProperty.call(MODES, data.kind) ? MODES[data.kind] : null;
    if (!draw) {
      renderFallback(result, "This viewer page (Ogive " + VERSION + ") doesn't draw \u201c" + data.kind +
        "\u201d results.");
      return;
    }
    if (!T) throw new Error("three.js didn't load, so the 3D view can't run here");
    teardown();
    app.innerHTML = "";
    app.className = data.kind; // the layout depends on the mode (see viewer.html)
    app.style.height = fitHeight(HEIGHTS[data.kind] || 560) + "px";
    var stage = el("div", { class: "stage" });
    app.appendChild(stage);
    view = new View(stage);
    draw(data, stage);
  }

  function renderDesign(d, stage) {
    var model = d.model, assumed = d.assumed || [];
    var rocket = buildRocket(model, assumed);
    view.root.add(rocket.group);
    var options = { cgcp: true, exploded: false, byMass: false, xray: false };
    var R = d.max_diameter / 2;

    var cg = marker(cssVar("--cg") || "#1f5fbf", R * 0.55, true);
    cg.position.set(d.cg, 0, 0);
    var cgB = marker(cssVar("--cg") || "#1f5fbf", R * 0.45, false);
    cgB.position.set(d.cg_burnout, 0, 0);
    var cp = marker(cssVar("--cp") || "#c0392b", R * 0.55, true);
    cp.position.set(d.cp, 0, 0);
    [cg, cgB, cp].forEach(function (m) { view.root.add(m); });
    // CP labelled above its marker, CG below, CG burnout one row lower: no label covers a marker.
    var tags = [
      view.tag("CP " + mm(d.cp) + " mm", "cp", cp.position, -24),
      view.tag("CG " + mm(d.cg) + " mm", "cg", cg.position, 24),
      view.tag("CG burnout " + mm(d.cg_burnout) + " mm", "cg", cgB.position, 48),
    ];
    // ASSUMED parts are always labelled; every part and the motor get name + mass when exploded.
    var partTags = [];
    var labelled = {};
    rocket.meshes.forEach(function (m) {
      var u = m.userData;
      if (u.assumed && !labelled[u.name]) {
        labelled[u.name] = true;
        partTags.push({ mesh: m, top: true,
          tag: view.tag(u.name + ": ASSUMED", "assumed", new T.Vector3(), -20) });
      }
      if (u.home) {
        partTags.push({ mesh: m, top: false, exploded: true,
          tag: view.tag(u.name + " " + grams(u.mass), "part", new T.Vector3(), 16) });
      }
    });

    // Frame everything drawn; exploding re-frames from the same direction to include the shelves.
    function fitAll(theta, phi) {
      view.scene.updateMatrixWorld(true);
      view.frame(new T.Box3().setFromObject(rocket.group), theta, phi);
    }
    var wasExploded = false;

    function apply() {
      styleRocket(rocket, options, R);
      if (options.exploded !== wasExploded) {
        wasExploded = options.exploded;
        fitAll(view.spherical.theta, view.spherical.phi);
      }
      [cg, cgB, cp].forEach(function (m) { m.visible = options.cgcp; });
      tags.forEach(function (t) { t.visible = options.cgcp; });
      view.scene.updateMatrixWorld(true);
      partTags.forEach(function (p) {
        var box = new T.Box3().setFromObject(p.mesh);
        var c = box.getCenter(new T.Vector3());
        p.tag.position.set(c.x, p.top ? box.max.y : box.min.y, c.z);
        if (p.exploded) p.tag.visible = options.exploded;
      });
      legend.style.display = options.byMass ? "block" : "none";
      view.requestRender();
    }

    // Panels.
    var info = el("div", { class: "panel", id: "info" });
    info.appendChild(el("h1", {}, d.title));
    info.appendChild(el("div", { class: "stats" },
      mm(d.length) + " mm long, " + mm(d.max_diameter) + " mm diameter. Launch " + grams(d.launch_mass) +
      ", dry " + grams(d.dry_mass) + ". Motor " + (d.motors.join(", ") || "none") + "."));
    info.appendChild(el("div", { class: "stats" },
      "Stability " + d.stability.toFixed(2) + " cal at M0.30 (burnout " + d.stability_burnout.toFixed(2) +
      " cal). CG " + mm(d.cg) + " mm, CP " + mm(d.cp) + " mm from the tip."));
    stage.appendChild(info);

    var controls = el("div", { class: "panel", id: "controls" });
    [["cgcp", "CG / CP"], ["exploded", "Exploded internals"], ["byMass", "Colour by mass"],
     ["xray", "See-through body"]].forEach(function (pair) {
      var label = el("label");
      var box = el("input", { type: "checkbox" });
      box.checked = options[pair[0]];
      box.addEventListener("change", function () { options[pair[0]] = box.checked; apply(); });
      label.appendChild(box);
      label.appendChild(document.createTextNode(pair[1]));
      controls.appendChild(label);
    });
    var row = el("div", { class: "row" });
    [["3/4", 0.5, 1.25], ["Side", 0, Math.PI / 2], ["Top", 0, 0.05], ["Tail", Math.PI / 2, Math.PI / 2]]
      .forEach(function (v) {
        var b = el("button", { type: "button" }, v[0]);
        b.addEventListener("click", function () { fitAll(v[1], v[2]); });
        row.appendChild(b);
      });
    controls.appendChild(row);
    stage.appendChild(controls);

    var legend = el("div", { class: "panel", id: "legend" });
    legend.appendChild(el("div", {}, "Colour by mass (OpenRocket)"));
    legend.appendChild(el("div", { class: "bar" }));
    legend.appendChild(el("div", { class: "stats" }, "light = light, dark = heavy"));
    stage.appendChild(legend);

    var footer = el("div", { class: "panel", id: "footer" });
    footer.appendChild(el("div", {}, d.title + "  sha256 " + d.provenance.sha256.slice(0, 12) + "  " +
      d.provenance.backend + "  Ogive " + VERSION + ". Geometry, masses, CG and CP as OpenRocket computes them. " +
      "Drag to rotate, wheel to zoom, shift-drag to pan."));
    if (assumed.length) {
      footer.appendChild(el("div", { class: "warn" }, "ASSUMED (orange): " + assumed.join(", ") +
        ". Not measured; check before trusting."));
    }
    if (d.not_drawn && d.not_drawn.length) {
      footer.appendChild(el("div", {}, "Not drawn: " + d.not_drawn.join(", ") + "."));
    }
    stage.appendChild(footer);

    apply();
    fitAll(0.5, 1.25);
  }

  // ---------- versions mode (timeline) ----------
  function shortNames(titles) {
    var prefix = titles[0];
    titles.forEach(function (t) {
      while (prefix && t.indexOf(prefix) !== 0) prefix = prefix.slice(0, -1);
    });
    // Cut back to a word boundary: "blackhawk29_v2" and "_v3" share "blackhawk29_v", keep the "v".
    prefix = prefix.slice(0, Math.max(prefix.lastIndexOf("_"), prefix.lastIndexOf("-"),
      prefix.lastIndexOf(" ")) + 1);
    return titles.map(function (t) {
      var s = t.slice(prefix.length).replace(/\.ork$/i, "");
      return s || t.replace(/\.ork$/i, "");
    });
  }

  function signed(value, digits) {
    var text = Math.abs(value).toFixed(digits);
    if (Number(text) === 0) return "0";
    return (value > 0 ? "+" : "−") + text;
  }

  var METRICS = [
    ["Apogee (m)", function (v) { return v.flight.apogee; }, 0],
    ["Max Mach", function (v) { return v.flight.max_mach; }, 3],
    ["Max velocity (m/s)", function (v) { return v.flight.max_velocity; }, 0],
    ["Stability (cal)", function (v) { return v.stability; }, 2],
    ["At burnout (cal)", function (v) { return v.stability_burnout; }, 2],
    ["CG (mm)", function (v) { return v.cg * 1000; }, 0],
    ["CP (mm)", function (v) { return v.cp * 1000; }, 0],
    ["Launch mass (g)", function (v) { return v.launch_mass * 1000; }, 1],
    ["Dry mass (g)", function (v) { return v.dry_mass * 1000; }, 1],
    ["Length (mm)", function (v) { return v.length * 1000; }, 0],
  ];

  function ghostStyle(mesh) {
    var mat = mesh.material;
    mat.color.set(cssVar("--muted") || "#8a93a3");
    mat.emissive = new T.Color(0x000000);
    mat.transparent = true;
    mat.opacity = 0.2;
    mat.depthWrite = false;
  }

  function renderVersions(d) {
    var stage = view.container;
    var versions = d.versions;
    var names = shortNames(versions.map(function (v) { return v.title; }));
    var state = { index: versions.length - 1, against: "previous", cgcp: true, ghost: true, xray: false };
    var shown = {}, ghosts = {};
    function rocketFor(i) {
      if (!shown[i]) shown[i] = buildRocket(versions[i].model, []);
      return shown[i];
    }
    function ghostFor(i) {
      if (!ghosts[i]) {
        ghosts[i] = buildRocket(versions[i].model, []);
        ghosts[i].meshes.forEach(ghostStyle);
      }
      return ghosts[i];
    }
    var maxR = 0;
    versions.forEach(function (v) { maxR = Math.max(maxR, v.max_diameter / 2); });
    var cg = marker(cssVar("--cg") || "#1f5fbf", maxR * 0.55, true);
    var cgB = marker(cssVar("--cg") || "#1f5fbf", maxR * 0.45, false);
    var cp = marker(cssVar("--cp") || "#c0392b", maxR * 0.55, true);
    var cgWas = marker(cssVar("--cg") || "#1f5fbf", maxR * 0.4, false);
    var cpWas = marker(cssVar("--cp") || "#c0392b", maxR * 0.4, false);
    [cg, cgB, cp, cgWas, cpWas].forEach(function (m) { view.root.add(m); });
    var cpTag = view.tag("", "cp", cp.position, -24);
    var cgTag = view.tag("", "cg", cg.position, 24);
    var cgBTag = view.tag("", "cg", cgB.position, 48);

    // Panels: info over the 3D view, details on the right, the timeline along the bottom.
    var info = el("div", { class: "panel", id: "info" });
    stage.appendChild(info);
    var side = el("div", { class: "panel", id: "side" });
    app.appendChild(side);
    var timeline = el("div", { class: "panel", id: "timeline" });
    app.appendChild(timeline);

    var compareRow = el("div", { class: "row" });
    compareRow.appendChild(el("span", { class: "muted" }, "Compare with"));
    var againstButtons = {};
    [["previous", "Previous"], ["first", "First"]].forEach(function (pair) {
      var b = el("button", { type: "button" }, pair[1]);
      b.addEventListener("click", function () { state.against = pair[0]; update(); });
      againstButtons[pair[0]] = b;
      compareRow.appendChild(b);
    });
    side.appendChild(compareRow);
    var toggles = el("div", { class: "row" });
    [["cgcp", "CG / CP"], ["ghost", "Ghost"], ["xray", "See-through"]].forEach(function (pair) {
      var label = el("label");
      var box = el("input", { type: "checkbox" });
      box.checked = state[pair[0]];
      box.addEventListener("change", function () { state[pair[0]] = box.checked; update(); });
      label.appendChild(box);
      label.appendChild(document.createTextNode(pair[1]));
      toggles.appendChild(label);
    });
    side.appendChild(toggles);
    var cameraRow = el("div", { class: "row" });
    [["3/4", 0.3, 1.3], ["Side", 0, Math.PI / 2], ["Tail", Math.PI / 2, Math.PI / 2]].forEach(function (v) {
      var b = el("button", { type: "button" }, v[0]);
      b.addEventListener("click", function () { fitLongest(v[1], v[2]); });
      cameraRow.appendChild(b);
    });
    side.appendChild(cameraRow);
    var table = el("table");
    side.appendChild(table);
    var judge = el("div", { class: "judge" });
    side.appendChild(judge);
    var changes = el("div", { class: "changes" });
    side.appendChild(changes);
    var source = el("div", { class: "muted small" });
    side.appendChild(source);

    var slider = el("input", { type: "range", min: "0", max: String(versions.length - 1), step: "1",
      "aria-label": "Version" });
    slider.value = String(state.index);
    slider.addEventListener("input", function () { state.index = Number(slider.value); update(); });
    timeline.appendChild(slider);
    var ticks = el("div", { class: "ticks" });
    var tickButtons = versions.map(function (v, i) {
      var b = el("button", { type: "button", class: "tick" + (v.passed ? " pass" : " fail"), title: v.title });
      b.appendChild(el("span", { class: "dot" }));
      b.appendChild(document.createTextNode(names[i]));
      b.addEventListener("click", function () { state.index = i; slider.value = String(i); update(); });
      ticks.appendChild(b);
      return b;
    });
    timeline.appendChild(ticks);

    function compared() {
      if (state.index === 0) return -1;
      return state.against === "first" ? 0 : state.index - 1;
    }

    function fitLongest(theta, phi) {
      var longest = 0;
      versions.forEach(function (v, i) { if (v.length > versions[longest].length) longest = i; });
      var group = rocketFor(longest).group;
      var added = !group.parent;
      if (added) view.root.add(group);
      view.scene.updateMatrixWorld(true);
      view.frame(new T.Box3().setFromObject(group), theta, phi);
      if (added) view.root.remove(group);
    }

    function update() {
      var i = state.index, j = compared();
      var v = versions[i], was = j >= 0 ? versions[j] : null;
      var diff = j < 0 ? null : (state.against === "first" ? v.diff_first : v.diff_previous);
      var changed = {};
      if (diff) {
        diff.changed_fields.forEach(function (c) { changed[c.component] = true; });
        diff.added_components.forEach(function (n) { changed[n] = true; });
      }
      var motorChanged = !!(diff && diff.motors);

      // Scene: the shown version solid, the compared one as a ghost.
      view.root.children.slice().forEach(function (o) {
        if (o.userData.version !== undefined) view.root.remove(o);
      });
      var rocket = rocketFor(i);
      rocket.group.userData.version = i;
      view.root.add(rocket.group);
      styleRocket(rocket, { xray: state.xray }, v.max_diameter / 2);
      rocket.meshes.forEach(function (m) {
        var hit = changed[m.userData.name] || (motorChanged && m.userData.role === "motor");
        if (hit) {
          m.material.color.set(cssVar("--changed") || "#7c4dce");
          m.material.emissive = new T.Color(0x1a0b33);
        }
      });
      if (was && state.ghost) {
        var ghost = ghostFor(j);
        ghost.group.userData.version = j;
        view.root.add(ghost.group);
      }
      cg.position.set(v.cg, 0, 0);
      cgB.position.set(v.cg_burnout, 0, 0);
      cp.position.set(v.cp, 0, 0);
      [cg, cgB, cp].forEach(function (m) { m.visible = state.cgcp; });
      cgWas.visible = cpWas.visible = !!(was && state.cgcp && state.ghost);
      if (was) {
        cgWas.position.set(was.cg, 0, 0);
        cpWas.position.set(was.cp, 0, 0);
      }
      var dCp = was ? " (" + signed((v.cp - was.cp) * 1000, 0) + ")" : "";
      var dCg = was ? " (" + signed((v.cg - was.cg) * 1000, 0) + ")" : "";
      cpTag.div.textContent = "CP " + mm(v.cp) + " mm" + dCp;
      cgTag.div.textContent = "CG " + mm(v.cg) + " mm" + dCg;
      cgBTag.div.textContent = "CG burnout " + mm(v.cg_burnout) + " mm";
      [cpTag, cgTag, cgBTag].forEach(function (t) { t.visible = state.cgcp; });

      // Info.
      info.innerHTML = "";
      var head = el("h1", {}, v.title);
      head.appendChild(el("span", { class: "state " + (v.passed ? "pass" : "fail") }, v.passed ? " PASS" : " FAIL"));
      info.appendChild(head);
      info.appendChild(el("div", { class: "stats" }, "Version " + (i + 1) + " of " + versions.length +
        (was ? ". Ghost: " + was.title + "." : ". First version: nothing to compare.") +
        " Changed parts in violet."));

      // Table.
      table.innerHTML = "";
      var cols = el("colgroup");
      ["38%", "21%", "21%", "20%"].forEach(function (w) { cols.appendChild(el("col", { style: "width:" + w })); });
      table.appendChild(cols);
      var header = el("tr");
      ["", was ? names[j] : "", names[i], was ? "Change" : ""].forEach(function (h) { header.appendChild(el("th", {}, h)); });
      table.appendChild(header);
      var judgeRow = el("tr");
      judgeRow.appendChild(el("td", {}, "Judge"));
      judgeRow.appendChild(el("td", { class: was ? "state " + (was.passed ? "pass" : "fail") : "" },
        was ? (was.passed ? "PASS" : "FAIL") : ""));
      judgeRow.appendChild(el("td", { class: "state " + (v.passed ? "pass" : "fail") }, v.passed ? "PASS" : "FAIL"));
      judgeRow.appendChild(el("td"));
      table.appendChild(judgeRow);
      METRICS.forEach(function (row) {
        var now = row[1](v);
        var tr = el("tr");
        tr.appendChild(el("td", {}, row[0]));
        tr.appendChild(el("td", {}, was ? row[1](was).toFixed(row[2]) : ""));
        tr.appendChild(el("td", {}, now.toFixed(row[2])));
        tr.appendChild(el("td", {}, was ? signed(now - row[1](was), row[2]) : ""));
        table.appendChild(tr);
      });
      var motorRow = el("tr");
      motorRow.appendChild(el("td", {}, "Motor"));
      motorRow.appendChild(el("td", {}, was ? was.motors.join(", ") : ""));
      motorRow.appendChild(el("td", { class: motorChanged ? "hl" : "" }, v.motors.join(", ")));
      motorRow.appendChild(el("td"));
      table.appendChild(motorRow);

      // Judge findings for the shown version.
      judge.innerHTML = "";
      v.fails.forEach(function (f) { judge.appendChild(el("div", { class: "state fail" }, "FAIL " + f)); });
      v.warns.forEach(function (w) { judge.appendChild(el("div", { class: "warnline" }, "WARN " + w)); });

      // Field-level changes, as stored in the .ork (SI units).
      changes.innerHTML = "";
      if (diff) {
        changes.appendChild(el("div", { class: "muted" }, "Changed from " + was.title + " (values as stored, SI):"));
        var lines = [];
        diff.changed_fields.forEach(function (c) {
          lines.push(c.component + " · " + c.field + ": " + (c.base === null ? "none" : c.base) +
            " → " + (c.candidate === null ? "none" : c.candidate));
        });
        diff.added_components.forEach(function (n) { lines.push("Added: " + n); });
        diff.removed_components.forEach(function (n) { lines.push("Removed: " + n); });
        if (diff.motors) lines.push("Motor: " + was.motors.join(", ") + " → " + v.motors.join(", "));
        if (!lines.length) lines.push("No field changes.");
        var list = el("ul");
        lines.slice(0, 40).forEach(function (line) { list.appendChild(el("li", {}, line)); });
        if (lines.length > 40) list.appendChild(el("li", {}, "+" + (lines.length - 40) + " more"));
        changes.appendChild(list);
      }

      source.textContent = "Flights: " + v.provenance.backend + ", simulation “" + d.simulation +
        "”, seed " + d.seed + ", the same for every version" +
        (d.different_simulation.length ? " except " + d.different_simulation.join(", ") +
          " (no sim of that name; flew its first)" : "") +
        ". Geometry, CG, CP and masses from OpenRocket's model. sha256 " +
        v.provenance.sha256.slice(0, 12) + ". Ogive " + VERSION + ". " + d.advisory;

      againstButtons.previous.setAttribute("aria-pressed", String(state.against === "previous"));
      againstButtons.first.setAttribute("aria-pressed", String(state.against === "first"));
      tickButtons.forEach(function (b, k) {
        b.classList.toggle("on", k === i);
        b.classList.toggle("was", k === j);
      });
      view.requestRender();
    }

    update();
    fitLongest(0.3, 1.3);
  }

  // ---------- flight mode (replay of OpenRocket's simulation) ----------
  // OpenRocket's attitude: theta = elevation of the axis above the horizon, phi = azimuth from
  // north toward east (tests check this against the flight path). Scene: x east, y up, z south.
  function axisAt(theta, phi) {
    var c = Math.cos(theta);
    return new T.Vector3(c * Math.sin(phi), Math.sin(theta), -c * Math.cos(phi));
  }

  function clockText(t) {
    var tenths = Math.max(0, Math.round(t * 10));
    var minutes = Math.floor(tenths / 600), rest = (tenths - minutes * 600) / 10;
    return minutes + ":" + (rest < 10 ? "0" : "") + rest.toFixed(1);
  }

  function svgEl(tag, attrs) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  var PLAY_ICON = "M8 5v14l11-7z", PAUSE_ICON = "M6 5h4v14H6zM14 5h4v14h-4z",
    REPLAY_ICON = "M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z";

  function renderFlight(d) {
    var stage = view.container;
    var s = d.series, n = s.time.length, tEnd = s.time[n - 1];
    var L = d.length, R = d.max_diameter / 2;
    var state = { t: 0, playing: false, speed: 1, cam: "Chase" };

    // -- sampling (linear between OpenRocket's samples) --
    function index(t) {
      var lo = 0, hi = n - 1;
      while (hi - lo > 1) {
        var mid = (lo + hi) >> 1;
        if (s.time[mid] <= t) lo = mid; else hi = mid;
      }
      return lo;
    }
    function frac(i, t) {
      var span = s.time[i + 1] - s.time[i];
      return span > 0 ? Math.min(1, Math.max(0, (t - s.time[i]) / span)) : 0;
    }
    function sample(name, t) {
      var i = index(t), a = s[name][i], b = s[name][i + 1];
      if (a === null || a === undefined) return b === undefined ? null : b;
      if (b === null || b === undefined) return a;
      return a + (b - a) * frac(i, t);
    }
    function positionAt(t, out) {
      return out.set(sample("east", t), sample("altitude", t), -sample("north", t));
    }
    function attitudeAt(i) { // last simulated attitude at or before sample i
      while (i > 0 && (s.theta[i] === null || s.phi[i] === null)) i--;
      return axisAt(s.theta[i] === null ? Math.PI / 2 : s.theta[i], s.phi[i] || 0);
    }
    function axis(t) {
      var i = index(t);
      return attitudeAt(i).lerp(attitudeAt(Math.min(i + 1, n - 1)), frac(i, t)).normalize();
    }

    // -- what happened when: OpenRocket's events, Mach 1 read off its Mach series --
    function first(type) {
      for (var k = 0; k < d.events.length; k++) if (d.events[k].type === type) return d.events[k];
      return null;
    }
    var liftoff = first("Lift-off"), burnout = first("Motor burnout"), apogee = first("Apogee"),
      landing = first("Ground hit");
    function deviceName(e) {
      if (e.source_kind === "Parachute") return "Chute";
      if (e.source_kind === "Streamer") return "Streamer";
      return e.source || "Recovery";
    }
    var deployments = d.events.filter(function (e) { return e.type === "Recovery device deployment"; });
    var marks = [];
    if (liftoff) marks.push({ time: liftoff.time, name: "Liftoff" });
    d.mach_crossings.forEach(function (c) {
      marks.push({ time: c.time, name: c.direction === "up" ? "Mach 1" : "Subsonic", mach: true });
    });
    if (burnout) marks.push({ time: burnout.time, name: "Burnout" });
    if (apogee) marks.push({ time: apogee.time, name: "Apogee" });
    deployments.forEach(function (e) { marks.push({ time: e.time, name: deviceName(e) }); });
    if (landing) marks.push({ time: landing.time, name: "Landed" });
    marks.sort(function (a, b) { return a.time - b.time; });

    function captionAt(t) {
      if (landing && t >= landing.time) return ["Landed", ""];
      if (apogee && Math.abs(t - apogee.time) <= 0.8) return ["Apogee", ""];
      var under = null;
      deployments.forEach(function (e) { if (t >= e.time) under = e; });
      if (under) {
        return ["Under " + deviceName(under).toLowerCase(), "Attitude isn't simulated under recovery: drawn faded."];
      }
      var burning = !burnout || t < burnout.time;
      if ((sample("mach", t) || 0) >= 1) return ["SUPERSONIC", burning ? "Motor burning" : "Coasting"];
      return [burning ? "Motor burning" : "Coasting", ""];
    }

    // -- scene: ground, grid, rail, path, rocket --
    var box = new T.Box3();
    var points = [];
    for (var i = 0; i < n; i++) {
      var p = new T.Vector3(s.east[i] || 0, s.altitude[i] || 0, -(s.north[i] || 0));
      points.push(p);
      box.expandByPoint(p);
    }
    var reach = Math.max(200, Math.abs(box.min.x), Math.abs(box.max.x), Math.abs(box.min.z), Math.abs(box.max.z));
    var spacing = reach > 4000 ? 500 : 100;
    var size = Math.ceil((reach * 2.4) / spacing) * spacing;
    var ground = new T.Mesh(new T.PlaneGeometry(size * 20, size * 20),
      new T.MeshBasicMaterial({ color: cssVar("--ground") || "#e3ddcc", polygonOffset: true,
        polygonOffsetFactor: 2, polygonOffsetUnits: 2 }));
    ground.rotation.x = -Math.PI / 2;
    view.root.add(ground);
    var grid = new T.GridHelper(size, size / spacing, cssVar("--muted") || "#8a93a3", cssVar("--line") || "#cfc8b5");
    grid.position.y = 0.02;
    view.root.add(grid);
    view.root.add(new T.Line(new T.BufferGeometry().setFromPoints([new T.Vector3(),
      attitudeAt(0).multiplyScalar(d.conditions.rod_length)]), new T.LineBasicMaterial({ color: cssVar("--ink") })));
    var pathGeometry = new T.BufferGeometry().setFromPoints(points);
    view.root.add(new T.Line(pathGeometry, new T.LineBasicMaterial({ color: cssVar("--muted") || "#8a93a3",
      transparent: true, opacity: 0.45 })));
    var flown = new T.Line(pathGeometry.clone(), new T.LineBasicMaterial({ color: cssVar("--changed") || "#7c4dce" }));
    view.root.add(flown);

    var rocket = buildRocket(d.model, []);
    styleRocket(rocket, {}, R);
    var body = new T.Group();
    body.add(rocket.group);
    view.root.add(body);
    var basis = new T.Matrix4(), roll = new T.Quaternion(), X = new T.Vector3(1, 0, 0);
    function place(t) {
      rocket.group.position.x = -(sample("cg", t) || d.cg); // CG on the trajectory point
      positionAt(t, body.position);
      var a = axis(t);
      var xw = a.clone().negate(); // model +x runs nose to tail
      var ref = Math.abs(a.z) < 0.9 ? new T.Vector3(0, 0, 1) : new T.Vector3(1, 0, 0);
      var yw = ref.sub(xw.clone().multiplyScalar(ref.dot(xw))).normalize();
      basis.makeBasis(xw, yw, new T.Vector3().crossVectors(xw, yw));
      body.quaternion.setFromRotationMatrix(basis);
      body.quaternion.multiply(roll.setFromAxisAngle(X, sample("roll", t) || 0));
      var faded = d.attitude_until !== null && t > d.attitude_until;
      rocket.meshes.forEach(function (m) {
        m.material.transparent = faded;
        m.material.opacity = faded ? 0.45 : 1;
      });
    }

    // -- cameras: chase by default, the rest in a dropdown --
    var noseLength = 0;
    d.model.bodies.forEach(function (b) {
      if (b.profile[0][0] === 0) noseLength = b.profile[b.profile.length - 1][0];
    });
    var onboard = {
      Nose: { eye: new T.Vector3(noseLength * 0.9, R * 2.2, 0), look: new T.Vector3(L, R * 1.2, 0) },
    };
    var finSet = d.model.fin_sets[0];
    if (finSet) {
      var tip = finSet.points.reduce(function (a, b) { return b[1] > a[1] ? b : a; });
      var root = finSet.instances[0][1], x0 = finSet.instances[0][0];
      onboard.Fin = { eye: new T.Vector3(x0 + tip[0] + 0.01, root + tip[1] * 0.95, finSet.thickness * 2),
        look: new T.Vector3(0, root * 0.5, 0) };
    }
    var drift = new T.Vector3(points[n - 1].x, 0, points[n - 1].z);
    var side = drift.length() > 20 ? new T.Vector3(-drift.z, 0, drift.x).normalize() : new T.Vector3(0, 0, 1);
    var station = side.multiplyScalar(200).setY(1.7); // the viewer's choice, not data
    var CAMERAS = [["Chase", "Chase"], ["Overview", "Overview"]]
      .concat(onboard.Fin ? [["Fin", "Fin camera"]] : [], [["Nose", "Nose camera"], ["Ground", "Ground tracker"]]);

    function setCam(name) {
      state.cam = name;
      var cam = view.camera;
      cam.up.set(0, 1, 0);
      cam.fov = name === "Fin" || name === "Nose" ? 70 : 40;
      if (name === "Overview") view.frame(box.clone().expandByPoint(new T.Vector3()), 0.6, 1.2);
      var close = name === "Chase" || name === "Fin" || name === "Nose";
      cam.near = close ? 0.02 : Math.max(0.5, reach / 2000);
      cam.far = size * (close ? 6 : 40); // short range up close keeps the grid from flickering
      cam.updateProjectionMatrix();
      ground.scale.setScalar(name === "Overview" ? 0.05 : 1); // a map tile from above, a horizon up close
      camSelect.value = name;
      update();
    }
    view.onFrame = function () {
      var cam = view.camera, t = state.t;
      if (state.cam === "Overview") return; // orbit with the mouse
      body.updateMatrixWorld(true);
      if (state.cam === "Chase") {
        var a = axis(t);
        var across = new T.Vector3().crossVectors(a, new T.Vector3(0, 1, 0));
        if (across.lengthSq() < 1e-6) across.set(1, 0, 0);
        across.normalize();
        cam.up.set(0, 1, 0);
        cam.position.copy(body.position).addScaledVector(a, -L * 2.2).addScaledVector(across, L * 1.6)
          .add(new T.Vector3(0, L * 0.4, 0));
        cam.position.y = Math.max(cam.position.y, 0.3); // never under the ground
        cam.lookAt(body.position);
      } else if (state.cam === "Ground") {
        cam.up.set(0, 1, 0);
        cam.position.copy(station);
        cam.lookAt(body.position);
        // Zoom so the rocket fills about 40 % of the frame: at 2 km its body is thinner than a pixel.
        var dist = station.distanceTo(body.position);
        cam.fov = Math.min(50, Math.max(0.02, (2 * Math.atan((L * 1.25) / dist) * 180) / Math.PI));
        cam.updateProjectionMatrix();
      } else {
        var o = onboard[state.cam], m = rocket.group.matrixWorld;
        cam.position.copy(o.eye).applyMatrix4(m);
        cam.up.set(0, 1, 0).transformDirection(m);
        cam.lookAt(o.look.clone().applyMatrix4(m));
      }
    };

    // -- on the picture: HUD, phase caption, camera menu, the "not footage" line --
    var hud = el("div", { class: "fhud" });
    var readout = {};
    [["alt", "Altitude"], ["speed", "Speed"], ["mach", "Mach"], ["accel", "Accel"]].forEach(function (r) {
      var row = el("div", { class: "fhud-row" });
      row.appendChild(el("span", { class: "k" }, r[1]));
      readout[r[0]] = el("span", { class: "v" });
      row.appendChild(readout[r[0]]);
      hud.appendChild(row);
    });
    stage.appendChild(hud);
    var caption = el("div", { class: "caption", "aria-live": "polite" });
    var captionMain = el("div", { class: "caption-main" });
    var captionSub = el("div", { class: "caption-sub" });
    caption.appendChild(captionMain);
    caption.appendChild(captionSub);
    stage.appendChild(caption);
    var camLabel = el("label", { class: "fcam" });
    camLabel.appendChild(document.createTextNode("Camera "));
    var camSelect = el("select", { "aria-label": "Camera" });
    CAMERAS.forEach(function (c) { camSelect.appendChild(el("option", { value: c[0] }, c[1])); });
    camSelect.addEventListener("change", function () { setCam(camSelect.value); });
    camLabel.appendChild(camSelect);
    var camNote = el("div", { class: "fcam-note" });
    stage.appendChild(camNote);
    stage.appendChild(el("div", { class: "notfootage", title: d.title + ", sha256 " + d.provenance.sha256 +
      ". Positions, attitude, speeds and events are OpenRocket's; roll is integrated from its roll rate " +
      "and Mach 1 times are interpolated. Ogive " + VERSION + "." },
      d.label + " " + d.provenance.backend + ", “" + d.simulation + "”, seed " + d.seed + "."));
    var eventTags = marks.map(function (m) {
      return view.tag(m.name, "event", positionAt(m.time, new T.Vector3()), -18);
    });
    var rocketTag = view.tag("Rocket", "rocket", body.position, -16);

    // -- the player: the timeline across the full width, then play, time ... camera, speed --
    var bar = el("div", { class: "player" });
    var row = el("div", { class: "prow" });
    var play = el("button", { type: "button", class: "playbtn", "aria-label": "Play" });
    var icon = svgEl("svg", { viewBox: "0 0 24 24", width: "22", height: "22", "aria-hidden": "true" });
    var iconPath = svgEl("path", { d: PLAY_ICON, fill: "currentColor" });
    icon.appendChild(iconPath);
    play.appendChild(icon);
    play.addEventListener("click", toggle);
    row.appendChild(play);
    var clock = el("div", { class: "fclock" });
    row.appendChild(clock);
    row.appendChild(el("div", { class: "spacer" }));
    row.appendChild(camLabel);
    var tl = el("div", { class: "tl", role: "slider", tabindex: "0", "aria-label": "Replay time",
      "aria-valuemin": "0", "aria-valuemax": tEnd.toFixed(1) });
    var leads = svgEl("svg", { class: "leads", "aria-hidden": "true" });
    tl.appendChild(leads);
    var rail = el("div", { class: "rail" });
    var done = el("div", { class: "done" });
    rail.appendChild(done);
    tl.appendChild(rail);
    var labels = marks.map(function (m) {
      tl.appendChild(el("div", { class: "tick" + (m.mach ? " mach" : ""), style: "left:" + (m.time / tEnd) * 100 + "%" }));
      var b = el("button", { type: "button", class: "ev" + (m.mach ? " mach" : ""),
        title: m.name + " at " + clockText(m.time) }, m.name);
      b.addEventListener("click", function (e) { e.stopPropagation(); seek(m.time); });
      b.addEventListener("pointerdown", function (e) { e.stopPropagation(); });
      tl.appendChild(b);
      return { mark: m, el: b };
    });
    var knob = el("div", { class: "knob" });
    tl.appendChild(knob);
    bar.appendChild(tl);
    bar.appendChild(row);
    var speedSelect = el("select", { "aria-label": "Speed" });
    [0.5, 1, 2, 4].forEach(function (k) { speedSelect.appendChild(el("option", { value: String(k) }, k + "×")); });
    speedSelect.value = "1";
    speedSelect.addEventListener("change", function () { state.speed = Number(speedSelect.value); });
    row.appendChild(speedSelect);
    app.appendChild(bar);
    view.resize(); // the bar took its height from the picture

    // Event labels sit above the rail, left to right in time order, in up to two rows; a leader line
    // runs from each label to its tick so early events (four in the first 2 s) stay readable.
    function layoutLabels() {
      if (!tl.isConnected) { window.removeEventListener("resize", layoutLabels); return; }
      var W = tl.clientWidth;
      leads.setAttribute("width", String(W));
      leads.setAttribute("height", "40");
      while (leads.firstChild) leads.removeChild(leads.firstChild);
      var show = W >= 280;
      var rowEnd = [-1e9, -1e9];
      labels.forEach(function (lab) {
        lab.el.style.display = show ? "block" : "none";
        if (!show) return;
        var x = (lab.mark.time / tEnd) * W, w = lab.el.offsetWidth, row = 1, left = 0;
        for (var r = 0; r < 2; r++) {
          left = Math.max(0, x - w / 2, rowEnd[r] + 3);
          if (left + w <= W) { row = r; break; }
          left = Math.min(W - w, Math.max(0, x - w / 2));
        }
        rowEnd[row] = left + w;
        lab.el.style.left = left + "px";
        lab.el.style.top = row * 17 + "px";
        leads.appendChild(svgEl("line", { x1: String(left + w / 2), y1: String(row * 17 + 15), x2: String(x),
          y2: "36", class: lab.mark.mach ? "mach" : "" }));
      });
    }

    function seek(t) {
      state.t = Math.min(tEnd, Math.max(0, t));
      lastNow = null;
      update();
    }
    function timeAtPointer(e) {
      var rect = rail.getBoundingClientRect();
      return ((e.clientX - rect.left) / rect.width) * tEnd;
    }
    var dragging = false;
    tl.addEventListener("pointerdown", function (e) {
      dragging = true;
      try { tl.setPointerCapture(e.pointerId); } catch (err) { /* a pointer that already ended */ }
      seek(timeAtPointer(e));
    });
    tl.addEventListener("pointermove", function (e) { if (dragging) seek(timeAtPointer(e)); });
    tl.addEventListener("pointerup", function () { dragging = false; });
    tl.addEventListener("keydown", function (e) {
      var step = e.shiftKey ? 5 : 1;
      if (e.key === "ArrowRight") seek(state.t + step);
      else if (e.key === "ArrowLeft") seek(state.t - step);
      else if (e.key === "Home") seek(0);
      else if (e.key === "End") seek(tEnd);
      else return;
      e.preventDefault();
    });
    function onKey(e) {
      if (!view || app.className !== "flight") { document.removeEventListener("keydown", onKey); return; }
      var tag = (e.target && e.target.tagName) || "";
      if ((e.key === " " || e.code === "Space") && tag !== "BUTTON" && tag !== "SELECT" && tag !== "INPUT") {
        e.preventDefault();
        toggle();
      }
    }
    document.addEventListener("keydown", onKey);

    // -- playback: auto-plays once, no loop --
    var lastNow = null;
    function toggle() {
      if (state.playing) state.playing = false;
      else {
        if (state.t >= tEnd) state.t = 0;
        state.playing = true;
        lastNow = null;
        requestAnimationFrame(tick);
      }
      update();
    }
    function tick(now) {
      if (!state.playing || !view) return;
      if (lastNow !== null) state.t = Math.min(tEnd, state.t + ((now - lastNow) / 1000) * state.speed);
      lastNow = now;
      if (state.t >= tEnd) state.playing = false;
      update();
      if (state.playing) requestAnimationFrame(tick);
    }

    var shown = "";
    function update() {
      var t = state.t;
      place(t);
      flown.geometry.setDrawRange(0, index(t) + 2);
      var mach = sample("mach", t) || 0;
      readout.alt.textContent = Math.round(sample("altitude", t) || 0).toLocaleString("en-US") + " m";
      readout.speed.textContent = Math.round(sample("velocity", t) || 0) + " m/s";
      readout.mach.textContent = mach.toFixed(2);
      readout.mach.classList.toggle("super", mach >= 1);
      readout.accel.textContent = ((sample("acceleration", t) || 0) / 9.80665).toFixed(1) + " g";
      var c = captionAt(t);
      if (c[0] !== shown) {
        captionMain.textContent = c[0];
        caption.classList.toggle("super", c[0] === "SUPERSONIC");
        caption.classList.remove("flash");
        if (c[0] === "SUPERSONIC") { void caption.offsetWidth; caption.classList.add("flash"); }
        shown = c[0];
      }
      captionSub.textContent = c[1];
      var pct = (t / tEnd) * 100;
      done.style.width = pct + "%";
      knob.style.left = pct + "%";
      tl.setAttribute("aria-valuenow", t.toFixed(1));
      tl.setAttribute("aria-valuetext", clockText(t) + ", " + c[0]);
      clock.textContent = clockText(t) + " / " + clockText(tEnd);
      var ended = !state.playing && t >= tEnd;
      iconPath.setAttribute("d", state.playing ? PAUSE_ICON : ended ? REPLAY_ICON : PLAY_ICON);
      play.setAttribute("aria-label", state.playing ? "Pause" : ended ? "Replay" : "Play");
      var far = state.cam === "Overview" || state.cam === "Ground";
      eventTags.forEach(function (tag) { tag.visible = state.cam === "Overview"; });
      rocketTag.visible = far; // at these distances the rocket is smaller than a pixel
      camNote.textContent = state.cam === "Ground" ? "Tracker 200 m from the pad (the viewer's placement), " +
        Math.round(station.distanceTo(body.position)) + " m away." : state.cam === "Overview" ? "Drag to orbit." : "";
      view.requestRender();
    }

    setCam("Chase");
    layoutLabels();
    window.addEventListener("resize", layoutLabels);
    toggle(); // auto-play once
  }

  // ---------- start ----------
  if (!T) showMessage("three.js didn't load, so the 3D view can't run here.");
  request("ui/initialize", {
    protocolVersion: PROTOCOL,
    appInfo: { name: "Ogive viewer", version: VERSION },
    clientInfo: { name: "Ogive viewer", version: VERSION },
    capabilities: {},
    appCapabilities: { availableDisplayModes: ["inline", "fullscreen"] },
  }).then(function (result) {
    applyContext((result && result.hostContext) || {});
    initialized();
  }, initialized);

  // After the handshake, report our size whenever it changes (a loaded image, a new mode).
  function initialized() {
    notify("ui/notifications/initialized", {});
    reportSize();
    if (window.ResizeObserver) new ResizeObserver(function () { reportSize(); }).observe(app);
  }
})();
