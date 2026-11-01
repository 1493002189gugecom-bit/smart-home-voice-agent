import * as THREE from 'three';
import type { DeviceVisual } from './home-effects';
import { temperatureCaption } from './home-effects';
// A single procedural soft sprite serves all glow and airflow instances.
export function softTexture() {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 64;
  const ctx = canvas.getContext('2d')!;
  const g = ctx.createRadialGradient(32,32,0,32,32,32);
  g.addColorStop(0,'rgba(255,255,255,0.9)'); g.addColorStop(0.3,'rgba(255,255,255,0.5)'); g.addColorStop(1,'rgba(255,255,255,0)');
  ctx.fillStyle=g; ctx.fillRect(0,0,64,64);
  return new THREE.CanvasTexture(canvas);
}
export function createDeviceVisual(parent: THREE.Group, state: DeviceVisual, index: number, texture: THREE.Texture, lamp?: THREE.Object3D) {
  const root = new THREE.Group(); root.userData.deviceId=state.id; parent.add(root);
  const material = new THREE.MeshStandardMaterial({color:0xe6ded0,roughness:0.65});
  const makeBox = (x:number,y:number,z:number,w:number,h:number,d:number,m=material) => {
    const mesh=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),m); mesh.position.set(x,y,z); root.add(mesh); return mesh;
  };
  const led = new THREE.MeshBasicMaterial({color:0x758195});
  const light = new THREE.PointLight(0xffd7a0,0,4.6,2);
  const glow = new THREE.MeshBasicMaterial({map:texture,color:0xffd7a0,transparent:true,opacity:0,depthWrite:false});
  const halo = new THREE.Mesh(new THREE.PlaneGeometry(2.5,2.5),glow); halo.rotation.x=-Math.PI/2; halo.position.y=0.02;
  const labelCanvas = document.createElement('canvas'); labelCanvas.width=state.type==='ac'?512:state.type==='sensor'?400:192; labelCanvas.height=96;
  const labelTexture = new THREE.CanvasTexture(labelCanvas);
  const label = new THREE.Sprite(new THREE.SpriteMaterial({map:labelTexture,transparent:true,depthTest:false,toneMapped:false})); label.scale.set(1.6,0.3,1); root.add(label);
  if(state.type==='light') {
    root.position.set(index===0 ? 0 : -1+index*0.65,0,0);
    if(!lamp) makeBox(0,1.75,0,0.68,0.05,0.38);
    const bulb=new THREE.Mesh(new THREE.SphereGeometry(lamp?0.065:0.11,12,8),led); bulb.position.y=lamp?0:1.68; root.add(bulb);
    light.position.set(0,lamp?0:1.6,0); root.add(light,halo); label.position.set(0,lamp?0.35:2.05,0);
  } else if(state.type==='ac') {
    root.position.set(0.5-index*0.95,1.2, parent.userData.depth/2-0.14);
    makeBox(0,0,0,0.86,0.3,0.23); makeBox(0,-0.075,-0.125,0.7,0.06,0.02,new THREE.MeshStandardMaterial({color:0x424d62}));
    const dot=new THREE.Mesh(new THREE.SphereGeometry(0.018,8,6),led); dot.position.set(0.32,0,-0.13); root.add(dot); label.position.set(0,0.38,0);
  } else {
    root.position.set(state.type==='switch' ? 1.5-index*0.35 : -1.1-index*0.35,0.24,-0.9);
    makeBox(0,0,0,0.19,0.28,0.12);
    const dot=new THREE.Mesh(new THREE.SphereGeometry(0.026,8,6),led); dot.position.set(0,0.06,0.065); root.add(dot);
    if(state.type==='switch') for(const x of [-0.04,0.04]) makeBox(x,-0.035,0.065,0.015,0.05,0.005,new THREE.MeshStandardMaterial({color:0x465068}));
    label.position.set(0,0.3,0);
  }
  const labelAnchor=label.position.clone();
  const labelWidth=state.type==='ac'?108:state.type==='sensor'?92:52;
  // One Points draw call per AC, 24 particles. Fixed gentle motion is a state cue, not measured wind.
  const positions=new Float32Array(24*3); const geometry=new THREE.BufferGeometry(); geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));
  const alphas=new Float32Array(24); geometry.setAttribute('lifeAlpha',new THREE.BufferAttribute(alphas,1));
  const airMaterial=new THREE.PointsMaterial({map:texture,color:0x9eefff,size:22,sizeAttenuation:false,transparent:true,opacity:0,depthWrite:false});
  airMaterial.onBeforeCompile = shader => {
    shader.vertexShader='attribute float lifeAlpha; varying float vLifeAlpha;\n'+shader.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\n vLifeAlpha=lifeAlpha;');
    shader.fragmentShader='varying float vLifeAlpha;\n'+shader.fragmentShader.replace('#include <color_fragment>','#include <color_fragment>\n diffuseColor.a *= vLifeAlpha;');
  };
  const air=new THREE.Points(geometry,airMaterial); root.add(air); air.visible=false;
  const stripPositions:number[]=[], stripUvs:number[]=[], stripIndices:number[]=[];
  for(let strand=0;strand<5;strand++) for(let row=0;row<=16;row++) {
    const t=row/16;
    for(let edge=0;edge<2;edge++) { stripPositions.push((strand-2)*0.13+(edge-0.5)*0.075,-0.13-t*0.55,-0.16-t*1.05); stripUvs.push(edge,t); }
    if(row<16) { const b=strand*34+row*2; stripIndices.push(b,b+1,b+2,b+1,b+3,b+2); }
  }
  const stripGeometry=new THREE.BufferGeometry(); stripGeometry.setAttribute('position',new THREE.Float32BufferAttribute(stripPositions,3)); stripGeometry.setAttribute('uv',new THREE.Float32BufferAttribute(stripUvs,2)); stripGeometry.setIndex(stripIndices);
  const stripMaterial=new THREE.ShaderMaterial({transparent:true,depthWrite:false,side:THREE.DoubleSide,uniforms:{phase:{value:0},strength:{value:0},tint:{value:new THREE.Color(0x9eefff)}},
    vertexShader:'varying vec2 vUv; uniform float phase; void main(){vUv=uv;vec3 p=position;p.x+=sin(uv.y*7.0+phase*3.0+position.x*4.0)*uv.y*0.045;gl_Position=projectionMatrix*modelViewMatrix*vec4(p,1.0);}',
    fragmentShader:'varying vec2 vUv; uniform float phase; uniform float strength; uniform vec3 tint; void main(){float edge=pow(max(0.0,1.0-abs(vUv.x*2.0-1.0)),2.0);float fade=pow(sin(vUv.y*3.14159),1.4);float pulse=0.35+0.65*pow(0.5+0.5*sin(vUv.y*10.0-phase*4.0),2.0);gl_FragColor=vec4(tint,edge*fade*pulse*strength*0.35);}',
  });
  const ribbons=new THREE.Mesh(stripGeometry,stripMaterial); root.add(ribbons); ribbons.visible=false;
  let current=0, target=0, phase=0, available=false, airflow: DeviceVisual['airflow']=null, text='';
  function apply(s:DeviceVisual) {
    available=s.available; target=s.level;
    airflow=s.airflow;
    led.color.setHex(!s.available ? 0x758195 : s.type==='light' ? 0x9b8061 : s.on || s.type==='sensor' ? 0x8edfc4 : 0x384357);
    const next=!s.available ? '不可用' : s.type==='sensor' ? temperatureCaption(s) : s.type==='ac' ? !s.on ? '已关闭' : `${s.airflow==='cool'?'制冷':'送风'}${s.targetTemperature===null ? '' : ` · 设定 ${s.targetTemperature}°C`}` : s.type==='switch' ? s.on?'开启':'关闭' : `${s.on ? Math.round(s.level*100)+'%' : '关闭'}`;
    if(next!==text) { text=next; const ctx=labelCanvas.getContext('2d')!; ctx.clearRect(0,0,labelCanvas.width,96); ctx.fillStyle='rgba(16,23,42,0.85)'; ctx.fillRect(0,0,labelCanvas.width,96); ctx.font='44px sans-serif'; ctx.textAlign='center'; ctx.fillStyle=s.available?'#d8e4e9':'#a7afbb'; ctx.fillText(text,labelCanvas.width/2,65); labelTexture.needsUpdate=true; }
    if(s.type==='sensor' && s.temperature!==null) led.color.setHex(s.temperature>28?0xe8ac73:s.temperature<20?0x9eefff:0x8edfc4);
    if(!s.available) { current=0; light.intensity=0; glow.opacity=0; airMaterial.opacity=0; air.visible=false; ribbons.visible=false; }
  }
  apply(state);
  return {
    root, apply,
    revealLabel:(opacity:number)=>{label.visible=opacity>0.01;label.material.opacity=opacity;},
    sizeLabel:(worldPerPixel:number)=>label.scale.set(labelWidth*worldPerPixel,22*worldPerPixel,1),
    layoutLabel:(camera:THREE.Camera,width:number,height:number,occupied:Array<{x:number;y:number;w:number}>)=>{
      if(!label.visible) return;
      const projected=root.localToWorld(labelAnchor.clone()).project(camera);
      const x=(projected.x+1)*width/2;
      const baseY=(1-projected.y)*height/2;
      let y=Math.max(40,Math.min(height-50,baseY));
      for(let attempt=0;attempt<10;attempt++) {
        if(!occupied.some(r=>Math.abs(r.x-x)<(r.w+labelWidth)/2+4&&Math.abs(r.y-y)<26)) break;
        y=Math.max(40,Math.min(height-50,baseY+(attempt%2===0?-1:1)*(Math.floor(attempt/2)+1)*26));
      }
      occupied.push({x,y,w:labelWidth});
      projected.x=x/width*2-1; projected.y=1-y/height*2;
      label.position.copy(root.worldToLocal(projected.unproject(camera)));
    },
    room:state.room, type:state.type,
    dispose:()=> { root.removeFromParent(); root.traverse(o=>{if(o instanceof THREE.Mesh||o instanceof THREE.Points||o instanceof THREE.Sprite){o.geometry.dispose();(Array.isArray(o.material)?o.material:[o.material]).forEach(m=>m.dispose());}});labelTexture.dispose(); },
    inspect:()=>({room:state.room,type:state.type,available,target,current,airflow,airVisible:air.visible,lightIntensity:light.intensity,text,position:root.position.toArray(),labelVisible:label.visible,labelOpacity:label.material.opacity}),
    tick(dt:number) {
      if(lamp) { root.position.copy(parent.worldToLocal(lamp.getWorldPosition(new THREE.Vector3()))); halo.position.y=-root.position.y+0.02; }
      current += (target-current)*(1-Math.exp(-dt*9)); if(Math.abs(target-current)<0.002) current=target;
      if(state.type==='light') { light.intensity=available?current*6:0; glow.opacity=available?current*0.45:0; if(available) led.color.setRGB(0.22+current*0.78,0.2+current*0.6,0.17+current*0.25); else led.color.setHex(0x758195); }
      if(state.type==='ac') {
        phase+=dt*0.35;
        air.visible=available&&current>0.005; airMaterial.opacity=available?current*0.55:0;
        ribbons.visible=air.visible; stripMaterial.uniforms.phase.value=phase; stripMaterial.uniforms.strength.value=available?current:0;
        if(airflow) { const color=new THREE.Color(airflow==='cool'?0x9eefff:0xeaf1f4); airMaterial.color.lerp(color,1-Math.exp(-dt*9)); stripMaterial.uniforms.tint.value.copy(airMaterial.color); }
        for(let i=0;i<24;i++) { const age=(phase+i/24)%1; alphas[i]=Math.sin(age*Math.PI)**2; positions[i*3]=((i%6)/5-0.5)*(0.6+age*0.3)+Math.sin(phase*2+i)*age*0.06; positions[i*3+1]=-0.13-age*0.65; positions[i*3+2]=-0.18-age*1.1; }
        geometry.attributes.lifeAlpha.needsUpdate=true;
        geometry.attributes.position.needsUpdate=true;
      }
      return available && (Math.abs(current-target)>0.002 || (state.type==='ac'&&current>0.005));
    },
  };
}
