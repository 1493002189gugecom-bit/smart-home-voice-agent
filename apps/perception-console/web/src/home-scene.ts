import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import type { RoomId } from "./home-view-data";

type RoomSpec = { id: RoomId; x: number; z: number; width: number; depth: number; floor: number };
type RoomParts = { floor: THREE.MeshStandardMaterial; trim: THREE.MeshBasicMaterial; lamp: THREE.PointLight; glow: THREE.MeshBasicMaterial; personMarker: THREE.Group };

const rooms: RoomSpec[] = [
  { id: "living_room", x: 0, z: 0, width: 5.9, depth: 4.3, floor: 0x697187 },
  { id: "bedroom", x: -4.83, z: 0.55, width: 3.55, depth: 4.55, floor: 0x616c86 },
  { id: "kitchen", x: 1.34, z: 3.52, width: 3.22, depth: 2.62, floor: 0x6d7790 },
];

function box(parent: THREE.Group, position: [number, number, number], size: [number, number, number], color: number, radius = 0.045) {
  const material = new THREE.MeshStandardMaterial({ color, roughness: 0.8, metalness: 0.05 });
  const mesh = new THREE.Mesh(new RoundedBoxGeometry(size[0], size[1], size[2], 3, radius), material);
  mesh.position.set(...position);
  parent.add(mesh);
  return mesh;
}

function addFurniture(group: THREE.Group, id: RoomId) {
  if (id === "living_room") {
    // A quiet lounge silhouette: sofa, cushions, coffee table and a media wall.
    box(group, [-0.7, 0.24, 0.74], [2.35, 0.36, 0.9], 0xc1c6cf, 0.1);
    box(group, [-0.7, 0.54, 1.1], [2.35, 0.6, 0.24], 0xadb4c3, 0.08);
    box(group, [-1.85, 0.44, 0.71], [0.27, 0.52, 0.92], 0xabb3c1, 0.06);
    box(group, [0.45, 0.44, 0.71], [0.27, 0.52, 0.92], 0xabb3c1, 0.06);
    box(group, [-1.2, 0.44, 0.64], [0.46, 0.18, 0.47], 0xe4d9cb, 0.07);
    box(group, [-0.45, 0.44, 0.64], [0.46, 0.18, 0.47], 0xdad2c7, 0.07);
    box(group, [-0.63, 0.105, -0.58], [2.8, 0.02, 1.05], 0xaaa7a4, 0.12);
    box(group, [-0.63, 0.22, -0.55], [1.16, 0.18, 0.68], 0xd8c1a7, 0.1);
    box(group, [2.3, 0.16, 0], [0.22, 0.28, 2.45], 0x948d8b, 0.05);
    box(group, [2.48, 0.78, 0], [0.08, 1.05, 1.8], 0x1f2e45, 0.06);
    box(group, [2.54, 0.8, 0], [0.03, 0.82, 1.5], 0x4d6484, 0.03);
    box(group, [1.65, 0.14, -1.61], [0.84, 0.24, 0.58], 0xb1a495, 0.06);
    box(group, [1.65, 0.5, -1.61], [0.64, 0.54, 0.44], 0x557464, 0.15);
  } else if (id === "bedroom") {
    box(group, [0.15, 0.19, 0.2], [1.63, 0.27, 2.3], 0xa88f82, 0.07);
    box(group, [0.15, 0.42, 0.17], [1.49, 0.2, 2.08], 0xe2dddc, 0.09);
    box(group, [-0.25, 0.56, -0.58], [0.55, 0.13, 0.4], 0xf4ece7, 0.08);
    box(group, [0.52, 0.56, -0.58], [0.55, 0.13, 0.4], 0xf4ece7, 0.08);
    box(group, [0.15, 0.65, 1.33], [1.7, 0.84, 0.18], 0xa5a8b7, 0.06);
    box(group, [-1.35, 0.58, -0.94], [0.54, 1.14, 1.48], 0x9d9ca7, 0.05);
    box(group, [1.28, 0.24, 0.77], [0.45, 0.42, 0.5], 0xc7b9a5, 0.05);
  } else {
    box(group, [0.06, 0.4, 0.35], [2.27, 0.72, 0.68], 0xb0abb0, 0.05);
    box(group, [0.06, 0.79, 0.35], [2.35, 0.08, 0.82], 0xded9d1, 0.035);
    box(group, [0.08, 0.4, -0.75], [1.28, 0.7, 0.7], 0xbeb8b4, 0.06);
    box(group, [0.08, 0.8, -0.75], [1.39, 0.08, 0.82], 0xe5dfd8, 0.045);
    for (const x of [-0.57, 0.67]) box(group, [x, 0.86, 0.35], [0.32, 0.045, 0.32], 0x353d4b, 0.02);
    box(group, [1.2, 0.32, -0.86], [0.44, 0.5, 0.5], 0xb39f8f, 0.05);
  }
}

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

  const glowMaterial = new THREE.MeshBasicMaterial({ color: 0xe6caa2, transparent: true, opacity: 0.36, depthWrite: false });
  const glow = new THREE.Mesh(new THREE.CircleGeometry(Math.min(spec.width, spec.depth) * 0.38, 32), glowMaterial);
  glow.rotation.x = -Math.PI / 2;
  glow.position.y = 0.013;
  group.add(glow);
  const lamp = new THREE.PointLight(0xffdeb4, 0, 5.8, 1.5);
  lamp.position.set(0, 2.1, 0);
  group.add(lamp);
  addFurniture(group, spec.id);

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
  return { floor: floorMaterial, trim: trimMaterial, lamp, glow: glowMaterial, personMarker };
}

export type HomeSceneController = {
  update: (selected: RoomId, lights: Record<RoomId, boolean | null>, occupants: Record<RoomId, number>) => void;
  resetCamera: () => void;
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
  renderer.toneMappingExposure = 1.55;
  renderer.domElement.className = "house-canvas";
  renderer.domElement.setAttribute("aria-label", "三房间示意模型，可拖动旋转、滚轮缩放，点击房间查看状态");
  container.appendChild(renderer.domElement);

  const camera = new THREE.OrthographicCamera(-10, 10, 7, -7, 0.1, 100);
  const initialPosition = new THREE.Vector3(10.2, 13.6, 15.6);
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

  scene.add(new THREE.AmbientLight(0xb6c4eb, 2.25));
  const sun = new THREE.DirectionalLight(0xf4e4d4, 2.35);
  sun.position.set(-4, 11, -6);
  scene.add(sun);
  const blue = new THREE.DirectionalLight(0x7086db, 1.5);
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

  const render = () => renderer.render(scene, camera);
  const resize = () => {
    const width = Math.max(container.clientWidth, 1);
    const height = Math.max(container.clientHeight, 1);
    const aspect = width / height;
    const vertical = aspect < 1 ? 15 : 12.6;
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
    update(selected, lights, occupants) {
      for (const room of rooms) {
        const part = parts[room.id];
        const active = room.id === selected;
        const lit = lights[room.id] === true;
        part.floor.color.setHex(active ? 0x939db0 : room.floor);
        part.floor.emissive.setHex(active ? 0x253453 : 0x080c19);
        part.floor.emissiveIntensity = active ? 0.48 : 0.2;
        part.trim.color.setHex(active ? 0xd2dcff : 0x7788af);
        part.trim.opacity = active ? 1 : 0.62;
        part.lamp.intensity = lit ? 9 : 0;
        part.glow.opacity = lit ? 0.45 : 0;
        part.personMarker.visible = occupants[room.id] > 0;
      }
      render();
    },
    resetCamera() { camera.position.copy(initialPosition); controls.target.copy(initialTarget); camera.zoom = 1; camera.updateProjectionMatrix(); controls.update(); render(); },
    dispose() {
      observer.disconnect();
      controls.removeEventListener("change", render);
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", pointerUp);
      const geometries = new Set<THREE.BufferGeometry>();
      const materials = new Set<THREE.Material>();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.LineSegments) {
          geometries.add(object.geometry);
          const attached = Array.isArray(object.material) ? object.material : [object.material];
          attached.forEach((material) => materials.add(material));
        }
      });
      geometries.forEach((geometry) => geometry.dispose());
      materials.forEach((material) => material.dispose());
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
