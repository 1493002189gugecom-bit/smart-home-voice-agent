async page => {
 const checks=[];
 const inspect=()=>page.evaluate(()=>scene.inspect());
 const set=async changes=>{await page.evaluate(changes=>{for(const [id,patch] of Object.entries(changes)){const d=sample.devices.find(x=>x.id===id); if(patch.online!==undefined)d.online=patch.online; if(patch.state)d.state={...d.state,...patch.state};}refresh();},changes);};
 const check=(ok,name)=>{if(!ok)throw Error(name);checks.push(name);};
 await page.waitForFunction(()=>scene.inspect().models.length===21);
 check((await inspect()).models.length===21,'21 loaded furniture instances');
 for(const brightness of [25,50,100]) {await set({living_room_light:{state:{brightness}}});await page.waitForTimeout(650);const d=(await inspect()).devices;check(Math.abs(d.find(x=>x.id==='living_room_light').current-brightness/100)<0.006,brightness+'% smooth brightness');check(d.find(x=>x.id==='bedroom_light').target===0.5,'independent second light');}
 const before=(await inspect()).devices.find(x=>x.id==='living_room_light').current;
 await page.evaluate(()=>{for(let i=0;i<20;i++)refresh();});
 check((await inspect()).devices.find(x=>x.id==='living_room_light').current===before,'repeated snapshots preserve transition');
 await set({living_room_ac:{state:{mode:'fan_only'}}});await page.waitForTimeout(100);check((await inspect()).devices.find(x=>x.id==='living_room_ac').airflow==='fan','fan mode');
 await set({living_room_ac:{state:{on:false}}});let d=(await inspect()).devices.find(x=>x.id==='living_room_ac');check(d.current>0 && d.target===0,'off begins natural fade');await page.waitForTimeout(800);check(!(await inspect()).devices.find(x=>x.id==='living_room_ac').airVisible,'off dissipates');
 await set({bedroom_ac:{online:false},living_room_light:{online:false}});d=(await inspect()).devices;check(d.filter(x=>['bedroom_ac','living_room_light'].includes(x.id)).every(x=>!x.available&&!x.airVisible&&x.lightIntensity===0),'offline stops operational cues');
 await set({kitchen_ac:{state:{mode:'unknown'}}});check(!(await inspect()).devices.find(x=>x.id==='kitchen_ac').available,'unknown mode unavailable');
 await page.evaluate(()=>{sample.devices.push({id:'second_light',room_id:'living_room',type:'light',online:true,state:{on:true,brightness:25}});refresh();});await page.waitForTimeout(800);check((await inspect()).devices.find(x=>x.id==='second_light').current===0.25,'multiple devices in room bind distinct IDs');
 const client=await page.context().newCDPSession(page);await client.send('Emulation.setPageVisibilityOverride',{visibilityState:'hidden'}).catch(()=>{});
 // CDP support varies: dispatching the visibility event with hidden getter exercises the exact handler.
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});check(!(await inspect()).animationScheduled,'hidden page cancels animation');
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
 await page.evaluate(()=>{clearInterval(keep);sample.generated_at_ms=Date.now()-6000;scene.update('living_room',sample,false,{living_room:1,bedroom:0,kitchen:0});});await page.waitForTimeout(100);check((await inspect()).devices.every(x=>!x.available&&!x.airVisible&&x.lightIntensity===0),'expired snapshot stops all cues');check(!(await inspect()).animationScheduled,'expired idle scene stops RAF');
 await page.evaluate(()=>{sample.generated_at_ms=Date.now();refresh();});check((await inspect()).devices.some(x=>x.available),'fresh snapshot recovers');
 await page.evaluate(()=>scene.dispose());let end=await inspect();check(end.disposed&&!end.animationScheduled&&end.memory.geometries===0&&end.memory.textures<=1,'dispose frees scene resources (one internal Three.js DFG LUT counter remains)');
 return {checks,final:end};
}