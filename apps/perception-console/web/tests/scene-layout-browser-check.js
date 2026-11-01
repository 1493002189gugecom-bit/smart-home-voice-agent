async page=>{
 const devices=['living_room','bedroom','kitchen'].flatMap((room,i)=>[{id:room+'_light',name:['客厅灯','卧室灯','厨房灯'][i],type:'light',room_id:room,online:true,state:{on:true,brightness:[100,50,25][i]}},{id:room+'_ac',name:['客厅空调','卧室空调','厨房空调'][i],type:'ac',room_id:room,online:true,state:{on:true,mode:i===1?'fan_only':'cool',target_temp:24}}]);
 devices.push({id:'desk_plug',name:'智能插座',room_id:'living_room',type:'switch',online:true,state:{on:true}},{id:'indoor_temperature',name:'室内温度',room_id:'living_room',type:'sensor',online:true,state:{temperature:25.5}});
 await page.route('**/api/home/snapshot',route=>route.fulfill({json:{backend:"memory",generated_at_ms:Date.now(),rooms:[],devices,persons:[{id:'demo',display_name:'模拟人物',location_known:true,room_id:'living_room'}]}}));
 await page.reload();await page.waitForTimeout(900);
 const results=[];
 for(const [width,height] of [[1280,800],[1200,800],[1024,768],[768,1024]]){
  await page.setViewportSize({width,height});await page.waitForTimeout(250);
  await page.evaluate(()=>{let b=document.querySelector('#simulation-banner');if(!b){b=document.createElement('div');b.id='simulation-banner';b.style='position:fixed;top:0;right:0;padding:8px;background:#574321;color:white;z-index:999';document.body.append(b);}b.textContent='模拟确认快照 · 非真实设备验收';});
  const check=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,canvas:{w:document.querySelector('canvas').clientWidth,h:document.querySelector('canvas').clientHeight},fallback:!!document.querySelector('.house-render-fallback')}));
  if(check.overflow||check.fallback)throw Error('layout '+width);
  await page.screenshot({path:'output/playwright/kaykit-tablet-'+width+'x'+height+'.png',fullPage:true});results.push({width,height,...check});
 }
 await page.unroute('**/api/home/snapshot');await page.reload();await page.waitForTimeout(500);
 return results;
}