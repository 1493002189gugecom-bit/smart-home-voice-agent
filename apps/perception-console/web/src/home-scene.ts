import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import type { HomeSnapshot, RoomId } from "./home-view-data";
import { furnitureLoader } from './home-furniture';
import { deviceVisuals, deviceSlots, snapshotFresh, labelReveal, SNAPSHOT_MAX_AGE } from './home-effects';
import { createDeviceVisual, softTexture } from './home-device-visuals';

type RoomSpec = { id: RoomId; x: number; z: number; width: number; depth: number; floor: number };
type RoomParts = { floor: THREE.MeshStandardMaterial; trim: THREE.MeshBasicMaterial; group: THREE.Group; personMarker: THREE.Group };

const rooms: RoomSpec[] = [
  { id: "living_room", x: 0, z: 0, width: 5.9, depth: 4.3, floor: 0x697187 },
  { id: "bedroom", x: -4.83, z: 0.55, width: 3.55, depth: 4.55, floor: 0x616c86 },
  { id: "kitchen", x: 1.34, z: 3.52, width: 3.22, depth: 2.62, floor: 0x6d7790 },
];

function createRoom(scene: THREE.Scene, spec: RoomSpec): RoomParts {
  const group = new THREE.Group();
  group.position.set(spec.x, 0, spec.z);
  group.userData.roomId = spec.id;
  scene.add(group);

  const floorMaterial = new THREE.MeshStandardMaterial({ color: spec.floor, roughness: 0.88, metalness: 0.04 });
  const floor = new THREE.Mesh(new RoundedBoxGeometry(spec.width, 0.15, spec.depth, 2, 0.06), floorMaterial);
  floor.position.y = -0.075;
  group.add(floor);

  const wallMaterial = new THREE.MeshPhysicalMaterial({ color: 0x7184ad, transparent: true, opacity: 0.21, roughness: 0.33, metalness: 0.25, side: THREE.DoubleSide, depthWrite: false });
  const backWall = new THREE.Mesh(new THREE.BoxGeometry(spec.width, 1.55, 0.06), wallMaterial);
  backWall.position.set(0, 0.77, spec.depth / 2);
  group.add(backWall);
  const sideWall = new THREE.Mesh(new THREE.BoxGeometry(0.06, 1.55, spec.depth), wallMaterial);
  sideWall.position.set(-spec.width / 2, 0.77, 0);
  group.add(sideWall);

  const trimMaterial = new THREE.MeshBasicMaterial({ color: 0x7788af, transparent: true, opacity: 0.72 });
  const frame = new THREE.Group();
  group.add(frame);
  const trim = (position: [number, number, number], size: [number, number, number]) => {
    const segment = new THREE.Mesh(new THREE.BoxGeometry(...size), trimMaterial);
    segment.position.set(...position);
    frame.add(segment);
  };
  trim([0, 1.55, spec.depth / 2], [spec.width, 0.035, 0.04]);
  trim([-spec.width / 2, 1.55, 0], [0.04, 0.035, spec.depth]);
  trim([spec.width / 2, 0.67, spec.depth / 2], [0.04, 1.34, 0.04]);
  trim([-spec.width / 2, 0.67, -spec.depth / 2], [0.04, 1.34, 0.04]);

  // A fixed room-level beacon; the camera does not provide a measured floor position.
  const personMarker = new THREE.Group();
  personMarker.position.set(spec.width * 0.25, 0, -spec.depth * 0.27);
  personMarker.visible = false;
  group.add(personMarker);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.38, 0.04, 8, 32), new THREE.MeshBasicMaterial({ color: 0x94ecd1 }));
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.1;
  personMarker.add(ring);
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, 0.9, 8), new THREE.MeshBasicMaterial({ color: 0x94ecd1, transparent: true, opacity: 0.75 }));
  stem.position.y = 0.58;
  personMarker.add(stem);
  const dot = new THREE.Mesh(new THREE.SphereGeometry(0.18, 16, 12), new THREE.MeshBasicMaterial({ color: 0xc8ffe7 }));
  dot.position.y = 1.12;
  personMarker.add(dot);
  group.userData.depth = spec.depth;
  return { floor: floorMaterial, trim: trimMaterial, group, personMarker };
}

export type HomeSceneController = {
  update: (selected: RoomId, home: HomeSnapshot, stale: boolean, occupants: Record<RoomId, number>) => void;
  resetCamera: () => void;
  inspect: () => Record<string, unknown>;
  dispose: () => void;
};

export function createHomeScene(container: HTMLElement, onRoomSelect: (id: RoomId) => void): HomeSceneController {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x10172a);
  scene.fog = new THREE.Fog(0x10172a, 18, 32);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.7));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  renderer.domElement.className = "house-canvas";
  renderer.domElement.setAttribute("aria-label", "三房间示意模型，可拖动旋转、滚轮缩放，点击房间查看状态");
  container.appendChild(renderer.domElement);

  const camera = new THREE.OrthographicCamera(-10, 10, 7, -7, 0.1, 100);
  // Look in from the cutaway's open +X/-Z sides, so inward-facing furniture
  // remains visible rather than looking through the rear wall at its backs.
  const initialPosition = new THREE.Vector3(10.2, 13.6, -13.24);
  const initialTarget = new THREE.Vector3(-1.32, 0, 1.18);
  camera.position.copy(initialPosition);
  camera.lookAt(initialTarget);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(initialTarget);
  controls.enableDamping = false;
  controls.enablePan = false;
  controls.minZoom = 0.78;
  controls.maxZoom = 2.1;
  controls.minPolarAngle = 0.38;
  controls.maxPolarAngle = 1.28;
  controls.update();

  scene.add(new THREE.AmbientLight(0xb6c4eb, 0.85));
  const sun = new THREE.DirectionalLight(0xf4e4d4, 1.25);
  sun.position.set(-4, 11, -6);
  scene.add(sun);
  const blue = new THREE.DirectionalLight(0x7086db, 0.5);
  blue.position.set(8, 5, 9);
  scene.add(blue);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(35, 35), new THREE.MeshBasicMaterial({ color: 0x10172a }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.28;
  scene.add(ground);
  const grid = new THREE.GridHelper(30, 30, 0x354368, 0x24304e);
  grid.position.y = -0.245;
  (grid.material as THREE.Material).transparent = true;
  (grid.material as THREE.Material).opacity = 0.28;
  scene.add(grid);
  const parts = Object.fromEntries(rooms.map((room) => [room.id, createRoom(scene, room)])) as Record<RoomId, RoomParts>;

  let disposed = false, frame = 0, previous = 0;
  let confirmed: HomeSnapshot = {}, staleState = true, expiry = 0;
  const effects = new Map<string, ReturnType<typeof createDeviceVisual>>();
  const slot=deviceSlots();
  const texture = softTexture();
  const render = () => {
    if(disposed || document.hidden) return;
    const width=Math.max(container.clientWidth,1), height=Math.max(container.clientHeight,1);
    const worldPerPixel=(camera.top-camera.bottom)/height/camera.zoom;
    camera.updateMatrixWorld(); scene.updateMatrixWorld(true);
    const occupied:Array<{x:number;y:number;w:number}>=[];
    const reveal=labelReveal(camera.zoom);
    effects.forEach(e=>{e.revealLabel(reveal);e.sizeLabel(worldPerPixel);e.layoutLabel(camera,width,height,occupied);});
    renderer.render(scene,camera);
  };
  const schedule = () => { if (!disposed && !document.hidden && !frame) frame = requestAnimationFrame(animate); };
  const animate = (now: number) => {
    frame=0; const dt=previous ? Math.min((now-previous)/1000,0.05) : 1/60; previous=now;
    let active=false; effects.forEach(effect => { if(effect.tick(dt)) active=true; }); render(); if(active) schedule(); else previous=0;
  };
  const syncDevices = () => {
    if(disposed) return;
    if (!snapshotFresh(confirmed,staleState)) Object.values(parts).forEach(p=>{p.personMarker.visible=false;});
    const values = deviceVisuals(confirmed, staleState);
    for (const value of values) {
      const old=effects.get(value.id);
      if(old && (old.room!==value.room || old.type!==value.type)) {old.dispose();effects.delete(value.id);}
      const index=slot(value.id,value.room,value.type);
      if (!effects.has(value.id)) effects.set(value.id, createDeviceVisual(parts[value.room].group,value,index,texture,value.type==='light'&&index===0?assets.lamp(value.room):undefined));
      effects.get(value.id)!.apply(value);
    }
    const present=new Set(values.map(v=>v.id)); effects.forEach((effect,id) => { if(!present.has(id)) effect.apply({id,room:'living_room',type:'light',available:false,on:false,level:0,airflow:null,temperature:null,targetTemperature:null}); });
    window.clearTimeout(expiry);
    const remaining = typeof confirmed.generated_at_ms==='number' ? confirmed.generated_at_ms+SNAPSHOT_MAX_AGE-Date.now()+1 : 0;
    if (!staleState && remaining>0) expiry=window.setTimeout(syncDevices,remaining);
    schedule(); render();
  };
  const visibility = () => { cancelAnimationFrame(frame); frame=0; previous=0; if(!document.hidden) syncDevices(); };
  document.addEventListener('visibilitychange',visibility);
  const assets = furnitureLoader(()=>{schedule();render();}); rooms.forEach(room=>assets.add(parts[room.id].group,room.id));
  const resize = () => {
    const width = Math.max(container.clientWidth, 1);
    const height = Math.max(container.clientHeight, 1);
    const aspect = width / height;
    const vertical = Math.max(10.2, 12.0 / aspect);
    camera.left = -vertical * aspect / 2;
    camera.right = vertical * aspect / 2;
    camera.top = vertical / 2;
    camera.bottom = -vertical / 2;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
    render();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  controls.addEventListener("change", render);
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let downX = 0;
  let downY = 0;
  const pointerDown = (event: PointerEvent) => { downX = event.clientX; downY = event.clientY; };
  const pointerUp = (event: PointerEvent) => {
    if (Math.hypot(event.clientX - downX, event.clientY - downY) > 7) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    for (const hit of raycaster.intersectObjects(scene.children, true)) {
      let object: THREE.Object3D | null = hit.object;
      while (object && !object.userData.roomId) object = object.parent;
      if (object?.userData.roomId) { onRoomSelect(object.userData.roomId as RoomId); return; }
    }
  };
  renderer.domElement.addEventListener("pointerdown", pointerDown);
  renderer.domElement.addEventListener("pointerup", pointerUp);
  resize();

  return {
    update(selected, home, stale, occupants) {
      if(disposed) return;
      for (const room of rooms) {
        const part = parts[room.id];
        const active = room.id === selected;

        part.floor.color.setHex(active ? 0x939db0 : room.floor);
        part.floor.emissive.setHex(active ? 0x253453 : 0x080c19);
        part.floor.emissiveIntensity = active ? 0.48 : 0.2;
        part.trim.color.setHex(active ? 0xd2dcff : 0x7788af);
        part.trim.opacity = active ? 1 : 0.62;

        part.personMarker.visible = occupants[room.id] > 0;
      }
      confirmed=home; staleState=stale; syncDevices();
    },
    resetCamera() { camera.position.copy(initialPosition); controls.target.copy(initialTarget); camera.zoom = 1; camera.updateProjectionMatrix(); controls.update(); render(); },
    inspect() { const models:string[]=[]; const textureIds=new Set<string>(); scene.traverse(o=>{if(o.userData.asset) models.push(o.userData.asset); if(o instanceof THREE.Mesh||o instanceof THREE.Sprite||o instanceof THREE.Points) (Array.isArray(o.material)?o.material:[o.material]).forEach(m=>Object.values(m).forEach(v=>{if(v instanceof THREE.Texture) textureIds.add(v.uuid);}));}); return {disposed,animationScheduled:frame!==0,models,textureCount:textureIds.size,devices:[...effects].map(([id,e])=>({id,...e.inspect()})),calls:renderer.info.render.calls,triangles:renderer.info.render.triangles,memory:{...renderer.info.memory}}; },
    dispose() {
      if(disposed) return;
      disposed=true; cancelAnimationFrame(frame); frame=0; window.clearTimeout(expiry);
      document.removeEventListener('visibilitychange',visibility);
      assets.dispose(); effects.clear();
      observer.disconnect();
      controls.removeEventListener("change", render);
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", pointerUp);
      const geometries = new Set<THREE.BufferGeometry>();
      const materials = new Set<THREE.Material>();
      const textures = new Set<THREE.Texture>();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.LineSegments || object instanceof THREE.Points || object instanceof THREE.Sprite) {
          geometries.add(object.geometry);
          const attached = Array.isArray(object.material) ? object.material : [object.material];
          attached.forEach((material) => { materials.add(material); Object.values(material).forEach(value=>{if(value instanceof THREE.Texture) textures.add(value);}); });
        }
      });
      geometries.forEach((geometry) => geometry.dispose());
      materials.forEach((material) => material.dispose());
      textures.forEach(t=>t.dispose());
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    },
  };
}
