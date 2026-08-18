import { useEffect, useRef } from "react";
import * as THREE from "three";

// A glowing particle sphere -- Jarvis's "presence". Points sit on a sphere and
// are pushed in and out by layered noise; the displacement and brightness scale
// with the current speech loudness (0 = idle drift, 1 = peak of an utterance).
//
// The level arrives as a ref rather than a plain prop on purpose. It updates 20
// times a second, and taking it as a prop would re-render the whole dashboard at
// 20Hz to feed an animation that already runs its own requestAnimationFrame loop.
// The parent writes ref.current; nothing re-renders; the loop reads it each frame.

const COUNT = 6000;
const RADIUS = 1;

// A soft round sprite so each point is a glowing dot, not a hard square.
function glowTexture(): THREE.Texture {
  const s = 64;
  const c = document.createElement("canvas");
  c.width = c.height = s;
  const ctx = c.getContext("2d")!;
  const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.25, "rgba(180,240,255,0.9)");
  g.addColorStop(1, "rgba(120,220,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, s, s);
  const tex = new THREE.Texture(c);
  tex.needsUpdate = true;
  return tex;
}

export default function Orb({ level }: { level: React.MutableRefObject<number> }) {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.z = 3.2;

    // Base unit-sphere directions (Fibonacci sphere = even coverage).
    const base = new Float32Array(COUNT * 3);
    const positions = new Float32Array(COUNT * 3);
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < COUNT; i++) {
      const y = 1 - (i / (COUNT - 1)) * 2;
      const r = Math.sqrt(1 - y * y);
      const t = golden * i;
      base[i * 3] = Math.cos(t) * r;
      base[i * 3 + 1] = y;
      base[i * 3 + 2] = Math.sin(t) * r;
    }
    positions.set(base);

    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const material = new THREE.PointsMaterial({
      size: 0.028,
      map: glowTexture(),
      color: new THREE.Color(0x22d3ee), // cyan
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      sizeAttenuation: true,
    });
    const points = new THREE.Points(geo, material);
    scene.add(points);

    // A faint inner core for depth.
    const core = new THREE.Mesh(
      new THREE.SphereGeometry(RADIUS * 0.82, 32, 32),
      new THREE.MeshBasicMaterial({
        color: 0x0a2a3a,
        transparent: true,
        opacity: 0.35,
        blending: THREE.AdditiveBlending,
      }),
    );
    scene.add(core);

    // Match the drawing buffer to the element size every frame. Doing it in the
    // loop (not just once/on ResizeObserver) self-heals the case where layout
    // isn't settled yet at mount time -- otherwise the buffer sticks at 0x0 and
    // the orb renders nothing.
    const dpr = Math.min(window.devicePixelRatio, 2);
    const fit = () => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      if (!w || !h) return;
      if (renderer.domElement.width !== Math.floor(w * dpr) ||
          renderer.domElement.height !== Math.floor(h * dpr)) {
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      }
    };

    // Smooth the incoming intensity so speech onset/stop eases in and out.
    let smooth = 0;
    let raf = 0;
    const clock = new THREE.Clock();
    const pos = geo.getAttribute("position") as THREE.BufferAttribute;

    const animate = () => {
      fit();
      const t = clock.getElapsedTime();
      const target = Math.max(0, Math.min(1, level.current));
      // Fast attack, slow release -- the same asymmetry a compressor uses, and
      // for the same reason: syllable onsets are sharp and need to land on the
      // frame they happen, while a symmetric filter slow enough to avoid
      // flicker is far too slow to track speech and just sits at its average.
      smooth += (target - smooth) * (target > smooth ? 0.35 : 0.1);

      // idle always has a little life; speaking adds punch.
      const amp = 0.04 + smooth * 0.22;
      const speed = 0.6 + smooth * 1.8;

      for (let i = 0; i < COUNT; i++) {
        const bx = base[i * 3];
        const by = base[i * 3 + 1];
        const bz = base[i * 3 + 2];
        // cheap layered noise from the point's own direction + time
        const n =
          Math.sin(bx * 3.0 + t * speed) *
            Math.cos(by * 3.0 - t * speed * 0.9) +
          Math.sin(bz * 4.0 + t * speed * 1.3) * 0.6;
        const r = 1 + amp * n;
        pos.array[i * 3] = bx * r;
        pos.array[i * 3 + 1] = by * r;
        pos.array[i * 3 + 2] = bz * r;
      }
      pos.needsUpdate = true;

      material.opacity = 0.55 + smooth * 0.45;
      material.size = 0.026 + smooth * 0.02;
      points.rotation.y = t * 0.12;
      points.rotation.x = Math.sin(t * 0.15) * 0.15;
      core.scale.setScalar(1 + smooth * 0.15);

      renderer.render(scene, camera);
      raf = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      cancelAnimationFrame(raf);
      geo.dispose();
      material.map?.dispose();
      material.dispose();
      core.geometry.dispose();
      (core.material as THREE.Material).dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, []);

  return <div className="orb" ref={mountRef} />;
}
