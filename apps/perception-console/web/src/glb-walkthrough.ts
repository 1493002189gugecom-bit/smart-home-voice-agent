import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import type { RoomId } from './home-view-data';
import { limitPitch, moveWalker, type WalkArea } from './walk-navigation';

export type WalkMode = 'overview' | 'walk';
export type WalkDirection = 'forward' | 'back' | 'left' | 'right';
export type WalkStatus = { phase:'loading'|'ready'|'error'; mode:WalkMode; room:RoomId; message?:string };
type NavigationRoom = { area:WalkArea; obstacles:WalkArea[]; spawn:THREE.Vector3 };
// Vite 将 public/assets 原样复制到 dist/assets。只从固定的本地路径中选择模型，
// glTF 入口读取 Blender 导出的 JSON、.bin 和共享贴图；默认入口继续使用原完整 GLB。
// 替换模型时需保留下面导航初始化所依赖的房间、地板和家具元数据。
const modelUrls={
  glb:'/assets/models/smart-home-layout-2026-10-12.glb',
  gltf:'/assets/models/smart-home-gltf/smart-home.gltf',
};

// 静态作业灯光：仅让灯罩上部成为 HDR 高亮源；原家具贴图和 UV 保留。
// 此查看器不读取设备快照，灯光只作效果演示，不表示真实设备已开启。
function addDemonstrationLampGlow(root:THREE.Object3D) {
  const lamps:THREE.Mesh[]=[];
  root.traverse(node=>{if(node instanceof THREE.Mesh && /^lamp_(table|standing)/.test(node.name))lamps.push(node);});
  for(const original of lamps) {
    original.geometry.computeBoundingBox();
    const box=original.geometry.boundingBox;if(!box||!original.parent)continue;
    const height=box.max.y-box.min.y,low=box.min.y+height*0.65,high=box.min.y+height*0.8;
    const glow=original.clone(false);
    const materials=(Array.isArray(original.material)?original.material:[original.material]).map(source=>{
      const material=source.clone();
      material.onBeforeCompile=shader=>{
        shader.vertexShader='varying float vLampHeight;\n'+shader.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\n vLampHeight=position.y;');
        shader.fragmentShader='varying float vLampHeight;\n'+shader.fragmentShader.replace('#include <opaque_fragment>',`outgoingLight += vec3(4.0,2.4,1.0)*smoothstep(${low.toFixed(6)},${high.toFixed(6)},vLampHeight);\n#include <opaque_fragment>`);
      };
      material.customProgramCacheKey=()=>`walkthrough-lamp-${low}-${high}`;
      return material;
    });
    glow.material=Array.isArray(original.material)?materials:materials[0];
    // 原对象继续保留，卸载时与克隆共同遍历释放，共享几何体与贴图只释放一次。
    original.visible=false;original.parent.add(glow);
  }
}

function releaseObjects(root:THREE.Object3D) {
  // 家具共用几何体、材质和图集；去重后释放 GPU 资源，并关闭加载器创建的 ImageBitmap。
  const geometries=new Set<THREE.BufferGeometry>(), materials=new Set<THREE.Material>(), textures=new Set<THREE.Texture>();
  root.traverse(o=>{
    if(o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
      geometries.add(o.geometry);
      for(const m of Array.isArray(o.material)?o.material:[o.material]) {
        materials.add(m);Object.values(m).forEach(value=>{if(value instanceof THREE.Texture)textures.add(value);});
      }
    }
  });
  geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());
  textures.forEach(t=>{t.dispose();if(typeof ImageBitmap!=='undefined' && t.image instanceof ImageBitmap)t.image.close();});
}

/**
 * 创建独立的 glTF/GLB 查看器：全屋模式用 OrbitControls，漫游模式用键盘/触控移动和拖动环视。
 * onStatus 将加载结果、模式和房间同步给 React；调用方卸载或重试时必须调用 dispose。
 */
export function createGlbWalkthrough(container:HTMLElement,onStatus:(status:WalkStatus)=>void,format:'glb'|'gltf'='glb') {
  const modelUrl=modelUrls[format],modelLabel=format==='gltf'?'glTF':'GLB';
  const scene=new THREE.Scene();scene.background=new THREE.Color(0x10182b);
  const renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:'high-performance'});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,1.5));
  renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.15;
  renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFShadowMap;
  const canvas=renderer.domElement;canvas.tabIndex=0;canvas.className='walkthrough-canvas';
  canvas.setAttribute('aria-label',`智能家居 ${modelLabel} 模型，进入漫游后可用 WASD 或方向键移动，拖动画面环视`);
  container.append(canvas);
  // YXZ 顺序让水平转向 yaw 与上下环视 pitch 组合时保持相机不侧倾。
  const camera=new THREE.PerspectiveCamera(48,1,0.035,100);camera.rotation.order='YXZ';
  // 先在 HDR 缓冲中绘制场景，提取高亮并模糊，再统一做色调映射与 sRGB 输出。
  // 阈值高于普通家具亮度，让光晕集中于灯罩；按需渲染，静止时不持续运行后处理。
  const composer=new EffectComposer(renderer);
  composer.renderTarget1.samples=composer.renderTarget2.samples=Math.min(2,renderer.capabilities.maxSamples);
  const bloom=new UnrealBloomPass(new THREE.Vector2(1,1),0.55,0.45,2.0);
  const output=new OutputPass();
  composer.addPass(new RenderPass(scene,camera));composer.addPass(bloom);composer.addPass(output);
  const controls=new OrbitControls(camera,canvas);controls.enableDamping=false;
  // OrbitControls 直接控制相机：左键围绕 target 旋转，中键/滚轮推拉缩放，右键平移。
  // 不启用阻尼，鼠标事件触发 change 后即可渲染，无需在静止时保持动画循环。
  controls.enableRotate=true;controls.enableZoom=true;controls.enablePan=true;
  controls.mouseButtons={LEFT:THREE.MOUSE.ROTATE,MIDDLE:THREE.MOUSE.DOLLY,RIGHT:THREE.MOUSE.PAN};
  controls.minDistance=1.5;controls.maxDistance=35;controls.maxPolarAngle=Math.PI*.47;
  scene.add(new THREE.AmbientLight(0xbdc9ed,0.95));
  const sun=new THREE.DirectionalLight(0xffe6c9,1.6);sun.position.set(-4,11,-6);sun.target.position.set(-1.3,0,1.3);
  sun.castShadow=true;sun.shadow.mapSize.set(1024,1024);sun.shadow.camera.left=-9;sun.shadow.camera.right=9;
  sun.shadow.camera.top=9;sun.shadow.camera.bottom=-9;sun.shadow.camera.far=35;sun.shadow.normalBias=0.025;
  scene.add(sun,sun.target);
  const rim=new THREE.DirectionalLight(0x92ace5,0.6);rim.position.set(8,5,9);scene.add(rim);
  const grid=new THREE.GridHelper(24,24,0x334366,0x22314b);grid.position.y=-0.17;
  scene.add(grid);
  let disposed=false, frame=0, previous=0, mode:WalkMode='overview', room:RoomId='living_room';
  let model:THREE.Group|null=null, yaw=Math.PI, pitch=-0.08;
  let pointer:{id:number;x:number;y:number}|null=null;
  const navigation=new Map<RoomId,NavigationRoom>();
  // 键盘与方向按钮分别记录，允许一根手指按住行走按钮，另一根手指拖动画布环视。
  const keys=new Set<string>(), directions=new Set<WalkDirection>();
  const render=()=>{if(!disposed&&!document.hidden)composer.render(0);};
  const moving=()=>mode==='walk'&&(directions.size>0||[...keys].some(key=>!key.startsWith('Shift')));
  // 持续移动时才请求下一帧；静止、缩放和环视由对应事件触发渲染，页面隐藏时停止动画。
  const schedule=()=>{if(!disposed&&!document.hidden&&!frame)frame=requestAnimationFrame(animate);};
  const animate=(now:number)=>{
    frame=0;
    const seconds=previous?(now-previous)/1000:1/60;previous=now;
    const current=navigation.get(room);
    if(mode==='walk' && current) {
      const forward=Number(keys.has('KeyW')||keys.has('ArrowUp')||directions.has('forward'))-Number(keys.has('KeyS')||keys.has('ArrowDown')||directions.has('back'));
      const strafe=Number(keys.has('KeyD')||keys.has('ArrowRight')||directions.has('right'))-Number(keys.has('KeyA')||keys.has('ArrowLeft')||directions.has('left'));
      const next=moveWalker(camera.position,{forward,strafe,yaw,seconds,fast:keys.has('ShiftLeft')||keys.has('ShiftRight')},current.area,current.obstacles);
      // 漫游保持入场时的视线高度，只在地面 X/Z 平面移动；碰撞计算在 walk-navigation.ts。
      camera.position.x=next.x;camera.position.z=next.z;render();
    }
    if(moving())schedule();else previous=0;
  };
  const stopMotion=()=>{previous=0;cancelAnimationFrame(frame);frame=0;};
  // 切换模式、失焦和切到后台时清空输入，避免遗漏 keyup/pointerup 后持续行走。
  const clearInput=()=>{keys.clear();directions.clear();pointer=null;stopMotion();};
  const publish=()=>{if(!disposed)onStatus({phase:model?'ready':'loading',mode,room});};
  const overview=()=>{
    if(!model)return;
    clearInput();mode='overview';controls.enabled=true;
    const box=new THREE.Box3().setFromObject(model),center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3());
    // 同时考虑水平和垂直视野，把整个模型放进电脑、横屏平板及竖屏窗口。
    const vertical=THREE.MathUtils.degToRad(camera.fov),horizontal=2*Math.atan(Math.tan(vertical/2)*camera.aspect);
    const distance=size.length()/2/Math.sin(Math.min(vertical,horizontal)/2)*1.08;
    camera.position.copy(center).add(new THREE.Vector3(1.15,1.36,-1.44).normalize().multiplyScalar(distance));
    controls.target.copy(center);controls.update();publish();render();
  };
  const enter=(id:RoomId)=>{
    // 当前示意模型没有连通门洞，通过房间按钮切换入场点；进入后关闭全屋轨道控制。
    const target=navigation.get(id);if(!target)return;
    clearInput();mode='walk';room=id;controls.enabled=false;
    camera.position.copy(target.spawn);yaw=Math.PI;pitch=-0.08;camera.rotation.set(pitch,yaw,0,'YXZ');
    canvas.focus({preventScroll:true});publish();render();
  };
  const resize=()=>{
    const width=Math.max(1,container.clientWidth),height=Math.max(1,container.clientHeight);
    camera.aspect=width/height;camera.updateProjectionMatrix();renderer.setSize(width,height,false);composer.setSize(width,height);
    if(mode==='overview'&&model)overview();else render();
  };
  const observer=new ResizeObserver(resize);observer.observe(container);
  const accepted=new Set(['KeyW','KeyS','KeyA','KeyD','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','ShiftLeft','ShiftRight']);
  const keyDown=(event:KeyboardEvent)=>{
    // 只有漫游画布获得焦点时才接收行走键，防止操作页面按钮时误移动或截获方向键。
    if(mode!=='walk'||document.activeElement!==canvas||!accepted.has(event.code))return;
    event.preventDefault();keys.add(event.code);schedule();
  };
  const keyUp=(event:KeyboardEvent)=>{keys.delete(event.code);if(!moving())stopMotion();};
  const pointerDown=(event:PointerEvent)=>{
    // 一次跟踪一个环视指针；捕获指针后，拖出画布仍能收到移动和释放事件。
    if(mode!=='walk'||pointer||(event.pointerType==='mouse'&&event.button!==0))return;
    canvas.focus({preventScroll:true});pointer={id:event.pointerId,x:event.clientX,y:event.clientY};canvas.setPointerCapture(event.pointerId);
  };
  const pointerMove=(event:PointerEvent)=>{
    if(!pointer||pointer.id!==event.pointerId||mode!=='walk')return;
    yaw-=(event.clientX-pointer.x)*0.004;pitch=limitPitch(pitch-(event.clientY-pointer.y)*0.004);
    pointer.x=event.clientX;pointer.y=event.clientY;camera.rotation.set(pitch,yaw,0,'YXZ');render();
  };
  const pointerEnd=(event:PointerEvent)=>{if(pointer?.id===event.pointerId)pointer=null;};
  const visibility=()=>{clearInput();if(!document.hidden)render();};
  canvas.addEventListener('pointerdown',pointerDown);canvas.addEventListener('pointermove',pointerMove);
  canvas.addEventListener('pointerup',pointerEnd);canvas.addEventListener('pointercancel',pointerEnd);canvas.addEventListener('lostpointercapture',pointerEnd);
  canvas.addEventListener('blur',clearInput);
  window.addEventListener('keydown',keyDown);window.addEventListener('keyup',keyUp);window.addEventListener('blur',clearInput);
  document.addEventListener('visibilitychange',visibility);controls.addEventListener('change',render);
  resize();publish();

  // 装载完整场景后建立导航数据；这是模型加载入口，不依赖原首页的程序化建模。
  new GLTFLoader().loadAsync(modelUrl).then(gltf=>{
    // 异步加载可能晚于 React 卸载完成，此时直接释放刚加载的模型。
    if(disposed){releaseObjects(gltf.scene);return;}
    // 包围盒必须基于世界变换计算，才能正确反映模型中家具的缩放、旋转和摆放位置。
    model=gltf.scene;addDemonstrationLampGlow(model);scene.add(model);model.updateMatrixWorld(true);
    model.traverse(o=>{if(o instanceof THREE.Mesh){o.castShadow=!o.material || !(Array.isArray(o.material)?o.material:[o.material]).some(m=>m.transparent);o.receiveShadow=true;}});
    const nodes:THREE.Object3D[]=[];model.traverse(o=>{if(o.userData.roomId)nodes.push(o);});
    const obstacles:WalkArea[]=[];
    model.traverse(o=>{
      // 导出时写入 GLB extras 的 asset/deviceId 在 Three.js 中变成 userData。
      // 以家具/设备的世界轴对齐包围盒作简化障碍，跳过地毯和脚下/头顶的物件。
      if(!o.userData.asset&&!o.userData.deviceId)return;
      if(String(o.userData.asset??'').startsWith('rug_'))return;
      const box=new THREE.Box3().setFromObject(o);
      if(box.max.y<0.12||box.min.y>1.25)return;
      obstacles.push({minX:box.min.x,maxX:box.max.x,minZ:box.min.z,maxZ:box.max.z});
    });
    for(const node of nodes) {
      const id=node.userData.roomId as RoomId;
      if(!['living_room','bedroom','kitchen'].includes(id))continue;
      // 导出约定：房间节点含 roomId，直接子节点的地板名为 <roomId>_floor。
      // 地板范围约束行走边界；以下入场点与 1.18 的视线高度按当前示意布局配置。
      const floor=node.children.find(o=>o.name===id+'_floor');if(!floor)continue;
      const box=new THREE.Box3().setFromObject(floor),center=box.getCenter(new THREE.Vector3());
      const spawn=id==='kitchen'?new THREE.Vector3(center.x,1.18,center.z+0.1):new THREE.Vector3(center.x+0.3,1.18,box.min.z+0.6);
      navigation.set(id,{area:{minX:box.min.x,maxX:box.max.x,minZ:box.min.z,maxZ:box.max.z},obstacles,spawn});
    }
    if(navigation.size!==3)throw Error('模型缺少房间地板信息');
    overview();
  }).catch(()=>{if(!disposed)onStatus({phase:'error',mode,room,message:`${modelLabel} 模型加载失败，请重试。`});});

  // React 页面通过这些方法切模式、切房间、重置视角和传递方向按钮的按下/释放状态。
  return {
    setBloom:(enabled:boolean)=>{bloom.enabled=enabled;render();},
    setMode:(next:WalkMode)=>{if(next==='overview')overview();else enter(room);},
    goRoom:enter,
    reset:()=>{if(mode==='overview')overview();else enter(room);},
    direction:(direction:WalkDirection,down:boolean)=>{
      if(mode!=='walk')return;
      if(down){directions.add(direction);schedule();}else{directions.delete(direction);if(!moving())stopMotion();}
    },
    dispose:()=>{
      // 卸载与重试共用清理流程：停止帧循环，移除监听器，释放模型及 WebGL 上下文。
      if(disposed)return;disposed=true;clearInput();observer.disconnect();controls.removeEventListener('change',render);controls.dispose();
      canvas.removeEventListener('pointerdown',pointerDown);canvas.removeEventListener('pointermove',pointerMove);
      canvas.removeEventListener('pointerup',pointerEnd);canvas.removeEventListener('pointercancel',pointerEnd);canvas.removeEventListener('lostpointercapture',pointerEnd);
      canvas.removeEventListener('blur',clearInput);window.removeEventListener('keydown',keyDown);window.removeEventListener('keyup',keyUp);window.removeEventListener('blur',clearInput);
      document.removeEventListener('visibilitychange',visibility);releaseObjects(scene);sun.shadow.map?.dispose();
      bloom.dispose();bloom.materialHighPassFilter.dispose();output.dispose();composer.dispose();
      renderer.dispose();renderer.forceContextLoss();canvas.remove();
    },
  };
}
