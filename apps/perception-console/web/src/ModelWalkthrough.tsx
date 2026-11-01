import { useEffect, useRef, useState, type PointerEvent } from 'react';
import { createGlbWalkthrough, type WalkDirection, type WalkStatus } from './glb-walkthrough';
import type { RoomId } from './home-view-data';
import './walkthrough.css';

const rooms:Array<[RoomId,string]>=[['living_room','客厅'],['bedroom','卧室'],['kitchen','厨房']];
// 页面只管理加载提示、模式/房间按钮及触控输入；Three.js 场景由 glb-walkthrough.ts 管理。
export default function ModelWalkthrough() {
  const host=useRef<HTMLDivElement>(null), viewer=useRef<ReturnType<typeof createGlbWalkthrough>|null>(null);
  const [status,setStatus]=useState<WalkStatus>({phase:'loading',mode:'overview',room:'living_room'}),[retry,setRetry]=useState(0);
  const [bloom,setBloom]=useState(true);
  const tablet=new URLSearchParams(window.location.search).has('tablet');
  // 作业入口 ?model=gltf 使用 Blender 导出的独立 glTF；其余值沿用原 GLB，不接受任意资源 URL。
  const format=new URLSearchParams(window.location.search).get('model')==='gltf'?'gltf':'glb';
  // 每次重试创建新控制器；卸载时先清理旧控制器，兼容 React StrictMode 的重复挂载检查。
  useEffect(()=>{
    if(!host.current)return;
    setStatus({phase:'loading',mode:'overview',room:'living_room'});
    setBloom(true);
    try {
      const controller=createGlbWalkthrough(host.current,setStatus,format);viewer.current=controller;
      return ()=>{viewer.current=null;controller.dispose();};
    } catch {setStatus({phase:'error',mode:'overview',room:'living_room',message:'当前浏览器无法创建 3D 视图。'});}
  },[retry,format]);
  const ready=status.phase==='ready';
  // 按钮捕获触控指针，手指移出按钮后仍能释放方向状态；cancel/失去捕获也走同一释放流程。
  // 只释放对应方向，保留另一根手指在画布上的环视操作。
  const press=(direction:WalkDirection,event:PointerEvent<HTMLButtonElement>)=>{event.preventDefault();event.currentTarget.setPointerCapture(event.pointerId);viewer.current?.direction(direction,true);};
  const release=(direction:WalkDirection)=>viewer.current?.direction(direction,false);
  return <main className="walkthrough-page">
    <header className="walkthrough-heading">
      <div><span>SMART HOME · MODEL EXPLORER</span><h1>走进我的家</h1><p>{format==='gltf'?'Blender 导出的 glTF 模型':'导出的 GLB 模型'} · 三个房间与家具布局</p></div>
      <a className="walkthrough-back" href={tablet?'/tablet':'/'}>返回我的家 ↗</a>
    </header>
    <section className="walkthrough-stage" aria-label="模型漫游">
      <div ref={host} className="walkthrough-render" />
      <div className="walkthrough-toolbar">
        <div className="walkthrough-modes" role="group" aria-label="查看方式">
          <button type="button" disabled={!ready} aria-pressed={status.mode==='overview'} onClick={()=>viewer.current?.setMode('overview')}>全屋查看</button>
          <button type="button" disabled={!ready} aria-pressed={status.mode==='walk'} onClick={()=>viewer.current?.setMode('walk')}>进入漫游</button>
        </div>
        <div className="walkthrough-tools">
          <button type="button" className="walkthrough-reset walkthrough-bloom" disabled={!ready} aria-pressed={bloom} title="比较 Bloom 泛光开启与关闭" onClick={()=>{viewer.current?.setBloom(!bloom);setBloom(!bloom);}}>泛光</button>
          <button type="button" className="walkthrough-reset" disabled={!ready} onClick={()=>viewer.current?.reset()}>重置视角</button>
        </div>
      </div>
      <div className="walkthrough-rooms" role="group" aria-label="进入房间">
        <span>进入房间</span>
        {rooms.map(([id,name])=><button type="button" key={id} disabled={!ready} aria-pressed={status.mode==='walk'&&status.room===id} onClick={()=>viewer.current?.goRoom(id)}>{name}</button>)}
      </div>
      {status.phase!=='ready'&&<div className="walkthrough-loading" role="status"><strong>{status.phase==='error'?'暂时无法显示模型':'正在打开模型'}</strong><p>{status.message??'正在加载房屋、家具与贴图…'}</p>{status.phase==='error'&&<button type="button" onClick={()=>setRetry(n=>n+1)}>重新加载</button>}</div>}
      {ready&&status.mode==='walk'&&<>
        <div className="walkthrough-crosshair" aria-hidden="true" />
        <div className="walkthrough-pad" role="group" aria-label="触控行走">
          {([['forward','向前','↑'],['left','向左','←'],['back','向后','↓'],['right','向右','→']] as const).map(([direction,label,symbol])=><button type="button" className={'pad-'+direction} key={direction} aria-label={label} onPointerDown={e=>press(direction,e)} onPointerUp={()=>release(direction)} onPointerCancel={()=>release(direction)} onLostPointerCapture={()=>release(direction)}>{symbol}</button>)}
        </div>
      </>}
      <div className="walkthrough-hint"><span className="walkthrough-status-dot" />{status.mode==='overview'?'左键旋转 · 滚轮缩放 · 右键平移':'拖动环视 · WASD / 方向键行走 · Shift 快走'}</div>
    </section>
    <footer className="walkthrough-footer"><span>静态示意模型 · 灯光仅作效果演示</span><span>位置与比例非实测 · 无门洞的房间通过按钮切换</span></footer>
  </main>;
}
