for(const panel of document.querySelectorAll('[data-api]')){
 const target=panel.querySelector('.result'),button=panel.querySelector('button');
 async function load(){button.disabled=true;target.textContent='Loading source…';try{
  const response=await fetch(panel.dataset.api,{signal:AbortSignal.timeout(45000)});if(!response.ok)throw Error();const result=await response.json();target.replaceChildren();
  const info=document.createElement('p');info.textContent=(result.status==='ok'?'Retrieved':result.status)+' · '+(result.source||'')+' · '+(result.retrieved_at||'No successful retrieval');target.append(info);
  if(result.message){const p=document.createElement('p');p.textContent=result.message;target.append(p)}
  if(result.data?.length){const wrap=document.createElement('div');wrap.className='table-wrap';const table=document.createElement('table');const header=document.createElement('tr');for(const key of Object.keys(result.data[0])){const th=document.createElement('th');th.textContent=key;header.append(th)}table.append(header);for(const row of result.data){const tr=document.createElement('tr');for(const value of Object.values(row)){const td=document.createElement('td');td.textContent=String(value??'Unknown');tr.append(td)}table.append(tr)}wrap.append(table);target.append(wrap)}else if(result.status==='ok'){const p=document.createElement('p');p.textContent='No matching records returned for this location and time.';target.append(p)}
 }catch{target.textContent='Unable to load this source. Check your internet connection and try again.'}finally{button.disabled=false}}
 button.addEventListener('click',load);load();
}
