// 房间边界和家具障碍统一使用世界坐标中的 X/Z 平面轴对齐矩形。
export type WalkArea = { minX: number; maxX: number; minZ: number; maxZ: number };
export type WalkInput = { forward: number; strafe: number; yaw: number; seconds: number; fast: boolean };
const clamp = (value:number,min:number,max:number) => Math.max(min,Math.min(max,value));
/** 计算一帧后的行走位置；保持纯计算，调用方负责更新 Three.js 相机。 */
export function moveWalker(position: {x:number;z:number}, input: WalkInput, area: WalkArea, obstacles: WalkArea[]) {
  // 用半径 0.14 的圆模拟行走者，给家具和房间边缘留出间距；单位沿用模型坐标。
  const radius=0.14;
  // 归一化斜向输入以保持速度一致；限制单帧时长，避免后台恢复或卡顿时突然跨越房间。
  const length=Math.max(1,Math.hypot(input.forward,input.strafe));
  const distance=clamp(input.seconds,0,0.05)*1.2*(input.fast?2:1)/length;
  // 将相机朝向下的前后/左右输入转换为世界位移；相机正前方是局部 -Z。
  const dx=(-Math.sin(input.yaw)*input.forward+Math.cos(input.yaw)*input.strafe)*distance;
  const dz=(-Math.cos(input.yaw)*input.forward-Math.sin(input.yaw)*input.strafe)*distance;
  // 把位移拆成不超过 0.04 的小步，逐步检查圆与障碍矩形最近点的距离，避免穿过薄障碍。
  const steps=Math.max(1,Math.ceil(Math.hypot(dx,dz)/0.04));
  const blocked=(x:number,z:number)=>obstacles.some(b=>Math.hypot(x-clamp(x,b.minX,b.maxX),z-clamp(z,b.minZ,b.maxZ))<radius);
  let {x,z}=position;
  for(let i=0;i<steps;i++) {
    // 分别尝试 X 和 Z 位移，遇到家具时可沿未被阻挡的轴滑动；边界预留行走者半径。
    const nextX=clamp(x+dx/steps,area.minX+radius,area.maxX-radius);
    if(!blocked(nextX,z)) x=nextX;
    const nextZ=clamp(z+dz/steps,area.minZ+radius,area.maxZ-radius);
    if(!blocked(x,nextZ)) z=nextZ;
  }
  return {x,z};
}
// 上下环视限制在 ±75°，避免仰俯过头造成视角翻转。
export function limitPitch(pitch: number) { return clamp(pitch,-Math.PI*5/12,Math.PI*5/12); }
