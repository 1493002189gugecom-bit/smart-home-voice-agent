import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import type { RoomId } from './home-view-data';
type Item = [string, number, number, number, number, number?];
export const furniture: Record<RoomId, Item[]> = {
  // Room back walls are +Z; model seats/doors/control faces are native +Z.
  // Put backs at the walls, keep fronts toward usable floor space.
  living_room: [
    ['couch_pillows',-0.7,0,1.4,2.5,Math.PI],
    ['armchair_pillows',1.55,0,0.85,0.9,Math.PI*1.25],
    ['rug_rectangle_A',-0.55,0.01,-0.35,2.9],
    ['table_low',-0.6,0.107,-0.1,1.2],
    ['cabinet_medium_decorated',-2.55,0,-0.6,1.4,Math.PI/2],
    ['lamp_standing',-2.25,0,1.75,0.5],
    ['cactus_medium_A',1.75,0,-1.55,0.5],
  ],
  bedroom: [
    ['bed_double_A',0.15,0,1.36,1.65,Math.PI],
    ['cabinet_medium',-1.375,0,-0.45,1.35,Math.PI/2],
    ['cabinet_small',1.3,0,1.87,0.5,Math.PI],
    ['lamp_table',1.3,0.5,1.87,0.3],
    ['rug_oval_A',0.3,0.01,0,1.5],
    ['pictureframe_large_A',-0.9,0.7,2.14,0.6,Math.PI],
  ],
  kitchen: [
    // Align the unit backs, separating the fridge from the stove; leave the
    // operation aisle between the countertop fronts and the dining table.
    ['kenney/kitchenCabinet',-1.075,0,0.81,0.8,Math.PI],
    ['kenney/kitchenSink',-0.275,0,0.81,0.8,Math.PI],
    ['kenney/kitchenStove',0.525,0,0.81,0.8,Math.PI],
    ['kenney/kitchenFridge',1.25,0,1.04,0.55,Math.PI],
    ['table_medium',-0.25,0,-0.71,1.05],
    ['chair_A',-1.15,0,-0.71,0.4,Math.PI/2],
    ['chair_A',0.65,0,-0.71,0.4,-Math.PI/2],
    ['lamp_table',-0.25,0.525,-0.71,0.25],
  ],
};
export function furnitureLoader(onChange: () => void) {
  const cache = new Map<string, Promise<THREE.Group>>();
  const roots = new Set<THREE.Group>();
  const holders = new Set<THREE.Group>();
  const lamps = new Map<RoomId,THREE.Object3D>();
  const manager = new THREE.LoadingManager();
  const loader = new GLTFLoader(manager);
  let disposed = false;
  let atlas: Promise<THREE.Texture> | null = null;
  const textures = new Set<THREE.Texture>();
  loader.register(parser => ({name:'SharedKayKitAtlas', loadTexture(index: number) {
    const image = parser.json.images?.[parser.json.textures?.[index]?.source];
    if (!image?.uri?.includes('furniturebits_texture')) return null;
    atlas ??= new THREE.TextureLoader().loadAsync('/assets/furniture/texture/furniturebits_texture.png').then(t => { t.flipY=false; t.colorSpace=THREE.SRGBColorSpace; if(disposed) t.dispose(); else textures.add(t); return t; });
    return atlas;
  }}));
  function release(root: THREE.Object3D) {
    const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>();
    root.traverse(o => { if (o instanceof THREE.Mesh) { geometries.add(o.geometry); (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { materials.add(m); Object.values(m).forEach(v => { if (v instanceof THREE.Texture) textures.add(v); }); }); } });
    geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose());
  }
  return {
    add(parent: THREE.Group, room: RoomId) {
      for (const [asset,x,y,z,width,rotation=0] of furniture[room]) {
        const holder = new THREE.Group(); holder.position.set(x,y,z); holder.rotation.y = rotation; parent.add(holder); holders.add(holder);
        const socket = new THREE.Object3D();
        if(asset.startsWith('lamp_')&&!lamps.has(room)) { holder.add(socket); socket.position.y=0.75; lamps.set(room,socket); }
        const fallback = new THREE.Mesh(new THREE.BoxGeometry(width,0.45,width*0.5),new THREE.MeshStandardMaterial({color:0xb6b0a9,roughness:0.85})); fallback.position.y = 0.225; holder.add(fallback);
        if (!cache.has(asset)) cache.set(asset, loader.loadAsync(`/assets/furniture/${asset}.${asset.startsWith('kenney/') ? 'glb' : 'gltf'}`).then(g => { if (disposed) release(g.scene); else roots.add(g.scene); return g.scene; }));
        cache.get(asset)!.then(source => {
          if (disposed) return;
          const model = source.clone(true); const bounds = new THREE.Box3().setFromObject(model); const size = bounds.getSize(new THREE.Vector3());
          const scale = width / Math.max(size.x,0.001); model.scale.setScalar(scale);
          const center = bounds.getCenter(new THREE.Vector3()); model.position.set(-center.x*scale,-bounds.min.y*scale,-center.z*scale);
          socket.position.y=size.y*scale*0.83;
          holder.remove(fallback); fallback.geometry.dispose(); fallback.material.dispose(); holder.add(model); holder.userData.asset = asset; onChange();
        }).catch(() => { holder.userData.loadFailed = asset; onChange(); });
      }
    },
    lamp(room: RoomId) { return lamps.get(room); },
    dispose() { disposed = true; holders.forEach(h=>{h.removeFromParent(); if (!h.userData.asset) release(h);}); holders.clear(); roots.forEach(release); roots.clear(); cache.clear(); textures.forEach(t => { t.dispose(); if (typeof ImageBitmap !== 'undefined' && t.image instanceof ImageBitmap) t.image.close(); }); textures.clear(); },
  };
}
