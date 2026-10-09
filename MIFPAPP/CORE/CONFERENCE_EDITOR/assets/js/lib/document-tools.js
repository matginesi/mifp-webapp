(function (global) {
  'use strict';

  const enc = new TextEncoder();
  const dec = new TextDecoder('utf-8');
  const A4 = { widthMm: 210, heightMm: 297, widthPt: 595.276, heightPt: 841.89 };

  function escXml(v) { return String(v == null ? '' : v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&apos;'); }
  function readU16(v,o){return v.getUint16(o,true);} function readU32(v,o){return v.getUint32(o,true);}
  function findEocd(bytes){const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);for(let i=bytes.length-22;i>=Math.max(0,bytes.length-65557);i--){if(readU32(v,i)===0x06054B50)return i;}throw new Error('Invalid DOCX/ZIP');}
  function zipIndex(bytes){const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),e=findEocd(bytes),count=readU16(v,e+10);let o=readU32(v,e+16);const m=new Map();for(let i=0;i<count;i++){if(readU32(v,o)!==0x02014B50)throw new Error('Invalid DOCX central directory');const method=readU16(v,o+10),compressedSize=readU32(v,o+20),size=readU32(v,o+24),nameLen=readU16(v,o+28),extraLen=readU16(v,o+30),commentLen=readU16(v,o+32),localOffset=readU32(v,o+42),name=dec.decode(bytes.subarray(o+46,o+46+nameLen));m.set(name,{method,compressedSize,size,localOffset});o+=46+nameLen+extraLen+commentLen;}return m;}
  async function inflateRaw(bytes){if(typeof DecompressionStream!=='function')throw new Error('This browser cannot decompress DOCX files');const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));return new Uint8Array(await new Response(stream).arrayBuffer());}
  async function readEntry(bytes,index,name){const meta=index.get(name);if(!meta)return null;const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),o=meta.localOffset;if(readU32(v,o)!==0x04034B50)throw new Error('Invalid DOCX entry');const nl=readU16(v,o+26),el=readU16(v,o+28),start=o+30+nl+el,c=bytes.subarray(start,start+meta.compressedSize);if(meta.method===0)return c.slice();if(meta.method===8)return inflateRaw(c);throw new Error('Unsupported DOCX compression');}
  async function unpackDocx(blob){const bytes=new Uint8Array(await blob.arrayBuffer()),index=zipIndex(bytes),out=[];for(const name of index.keys())out.push({path:name,data:await readEntry(bytes,index,name)});return out;}
  async function docxPlaceholders(blob){const entries=await unpackDocx(blob),found=new Set();for(const e of entries){if(!/^word\/.*\.xml$/i.test(e.path))continue;const text=dec.decode(e.data);(text.match(/(?:DOC_[A-Z0-9_]+|AFFILIATION_|COUNTRY_)/g)||[]).forEach(x=>found.add(x));}return Array.from(found).sort();}
  async function fillDocxTemplate(blob,replacements,media){const entries=await unpackDocx(blob);const out=[];const map=replacements||{};const keys=Object.keys(map).sort((a,b)=>b.length-a.length);const pattern=keys.length?new RegExp(keys.map(k=>k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|'),'g'):null;for(const e of entries){let data=e.data;if(pattern&&/^word\/.*\.xml$/i.test(e.path)){let text=dec.decode(data);text=text.replace(pattern,(key)=>escXml(map[key]));data=enc.encode(text);}if(media&&media[e.path]){const replacement=media[e.path];data=replacement instanceof Uint8Array?replacement:new Uint8Array(await replacement.arrayBuffer());}out.push({path:e.path,data});}return global.ZipLite.createBlob(out);}

  function dataUrlBytes(url){const comma=url.indexOf(',');const meta=url.slice(0,comma),body=url.slice(comma+1);const bin=atob(body);const bytes=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);return {bytes,mime:(meta.match(/^data:([^;]+)/)||[])[1]||'image/jpeg'};}
  async function canvasJpeg(canvas,quality){const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('Canvas export failed')),'image/jpeg',quality||0.92));return new Uint8Array(await blob.arrayBuffer());}
  function concat(parts){let n=0;parts.forEach(p=>n+=p.length);const out=new Uint8Array(n);let o=0;parts.forEach(p=>{out.set(p,o);o+=p.length;});return out;}
  function ascii(s){return enc.encode(s);}

  function emitProgress(cb,value,label){if(typeof cb==='function')cb(Math.max(0,Math.min(100,Math.round(value))),label||'');}

  async function buildPdfFromCanvases(canvases,onProgress){if(!canvases.length)throw new Error('No pages to export');const images=[];for(let i=0;i<canvases.length;i++){images.push({bytes:await canvasJpeg(canvases[i],.94),w:canvases[i].width,h:canvases[i].height,landscape:canvases[i].width>canvases[i].height});emitProgress(onProgress,8+Math.round(((i+1)/canvases.length)*46),'Encoding PDF page '+(i+1)+' / '+canvases.length);}
    const objects=[];const pageIds=[],imgIds=[],contentIds=[];let next=3;images.forEach(()=>{pageIds.push(next++);imgIds.push(next++);contentIds.push(next++);});
    objects[1]=ascii('<< /Type /Catalog /Pages 2 0 R >>');objects[2]=ascii('<< /Type /Pages /Count '+images.length+' /Kids ['+pageIds.map(id=>id+' 0 R').join(' ')+'] >>');
    images.forEach((img,i)=>{const p=pageIds[i],im=imgIds[i],co=contentIds[i],pw=img.landscape?A4.heightPt:A4.widthPt,ph=img.landscape?A4.widthPt:A4.heightPt;objects[p]=ascii('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 '+pw+' '+ph+'] /Resources << /XObject << /Im0 '+im+' 0 R >> >> /Contents '+co+' 0 R >>');const ih=ascii('<< /Type /XObject /Subtype /Image /Width '+img.w+' /Height '+img.h+' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length '+img.bytes.length+' >>\nstream\n'),it=ascii('\nendstream');objects[im]=concat([ih,img.bytes,it]);const stream='q\n'+pw+' 0 0 '+ph+' 0 0 cm\n/Im0 Do\nQ\n';objects[co]=ascii('<< /Length '+stream.length+' >>\nstream\n'+stream+'endstream');emitProgress(onProgress,56+Math.round(((i+1)/images.length)*34),'Writing PDF page '+(i+1)+' / '+images.length);});
    const parts=[ascii('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n')],offsets=[0];let pos=parts[0].length;for(let id=1;id<objects.length;id++){offsets[id]=pos;const h=ascii(id+' 0 obj\n'),t=ascii('\nendobj\n');parts.push(h,objects[id],t);pos+=h.length+objects[id].length+t.length;}const xref=pos;let xt='xref\n0 '+objects.length+'\n0000000000 65535 f \n';for(let id=1;id<objects.length;id++)xt+=String(offsets[id]).padStart(10,'0')+' 00000 n \n';xt+='trailer\n<< /Size '+objects.length+' /Root 1 0 R >>\nstartxref\n'+xref+'\n%%EOF';parts.push(ascii(xt));emitProgress(onProgress,100,'PDF ready');return new Blob([concat(parts)],{type:'application/pdf'});
  }

  async function buildDocxFromCanvases(canvases,onProgress){if(!canvases.length)throw new Error('No pages to export');const images=[];for(let i=0;i<canvases.length;i++){images.push(await canvasJpeg(canvases[i],.95));emitProgress(onProgress,8+Math.round(((i+1)/canvases.length)*52),'Encoding DOCX page '+(i+1)+' / '+canvases.length);}const landscape=canvases[0].width>canvases[0].height,pageW=landscape?10692000:7560000,pageH=landscape?7560000:10692000,twipW=landscape?16838:11906,twipH=landscape?11906:16838;const rels=images.map((_,i)=>'<Relationship Id="rId'+(i+1)+'" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/page-'+(i+1)+'.jpg"/>').join('');
    const paras=images.map((_,i)=>'<w:p><w:pPr><w:spacing w:before="0" w:after="0"/><w:jc w:val="center"/>'+(i<images.length-1?'<w:pageBreakBefore/>':'')+'</w:pPr><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="'+pageW+'" cy="'+pageH+'"/><wp:docPr id="'+(i+1)+'" name="Page '+(i+1)+'"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="0" name="page-'+(i+1)+'.jpg"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="rId'+(i+1)+'"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="'+pageW+'" cy="'+pageH+'"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>').join('');
    const document='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>'+paras+'<w:sectPr><w:pgSz w:w="'+twipW+'" w:h="'+twipH+'"'+(landscape?' w:orient="landscape"':'')+'/><w:pgMar w:top="0" w:right="0" w:bottom="0" w:left="0" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr></w:body></w:document>';
    const entries=[
      {path:'[Content_Types].xml',data:'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="jpg" ContentType="image/jpeg"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'},
      {path:'_rels/.rels',data:'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'},
      {path:'word/document.xml',data:document},
      {path:'word/_rels/document.xml.rels',data:'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+rels+'</Relationships>'}
    ];images.forEach((b,i)=>entries.push({path:'word/media/page-'+(i+1)+'.jpg',data:b}));emitProgress(onProgress,92,'Packing DOCX');const blob=await global.ZipLite.createBlob(entries);emitProgress(onProgress,100,'DOCX ready');return blob;
  }

  function canvasPage(widthMm,heightMm,scale){const c=document.createElement('canvas');c.width=Math.round(widthMm*scale);c.height=Math.round(heightMm*scale);return c;}
  function canvasA4(scale){return canvasPage(A4.widthMm,A4.heightMm,scale);}

  function mm(v,s){return v*s;}
  function drawImageContain(ctx,img,x,y,w,h){if(!img)return;const iw=Number(img.naturalWidth||img.width||0),ih=Number(img.naturalHeight||img.height||0);if(!iw||!ih)return;const r=Math.min(w/iw,h/ih),dw=iw*r,dh=ih*r;ctx.drawImage(img,x+(w-dw)/2,y+(h-dh)/2,dw,dh);}
  const FONT_SANS='"Noto Sans", "Aptos", "Segoe UI", "Liberation Sans", Arial, sans-serif';
  const FONT_SERIF='"Noto Serif", Baskerville, "Palatino Linotype", "Book Antiqua", Georgia, serif';
  function fontDecl(weight,size,family){return (weight||'500')+' '+size+'px '+(family||FONT_SANS);}
  function fontFit(ctx,text,maxWidth,start,min,weight,family){let size=start;do{ctx.font=fontDecl(weight,size,family);if(ctx.measureText(String(text||'')).width<=maxWidth)return size;size-=1;}while(size>min);return min;}
  function centerText(ctx,text,x,y,w,size,weight,color,family){ctx.fillStyle=color||'#111';ctx.font=fontDecl(weight,size,family);ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(text||''),x+w/2,y);}

  function wrapLines(ctx,text,maxWidth,maxLines){
    const words=String(text||'').trim().split(/\s+/).filter(Boolean);if(!words.length)return [];
    const lines=[];let line='';for(const word of words){const test=line?line+' '+word:word;if(ctx.measureText(test).width<=maxWidth||!line){line=test;continue;}lines.push(line);line=word;if(maxLines&&lines.length>=maxLines-1)break;}if(line&&(!maxLines||lines.length<maxLines))lines.push(line);return lines;
  }
  function centeredWrapped(ctx,text,x,y,w,lineHeight,size,weight,color,maxLines,family){ctx.fillStyle=color||'#111';ctx.font=fontDecl(weight,size,family);ctx.textAlign='center';ctx.textBaseline='middle';const lines=wrapLines(ctx,text,w,maxLines);const total=Math.max(0,(lines.length-1)*lineHeight);lines.forEach((line,i)=>ctx.fillText(line,x+w/2,y-total/2+i*lineHeight));return lines.length;}
  function rightLines(ctx,lines,rightX,topY,size,lineHeight,color,family){ctx.fillStyle=color||'#111';ctx.font=fontDecl('400',size,family);ctx.textAlign='right';ctx.textBaseline='top';lines.filter(Boolean).forEach((line,i)=>ctx.fillText(line,rightX,topY+i*lineHeight));}

  function pageCandidate(pageW,pageH,w,h,margin,gap,orientation){const cols=Math.max(1,Math.floor((pageW-2*margin+gap)/(w+gap))),rows=Math.max(1,Math.floor((pageH-2*margin+gap)/(h+gap))),perPage=cols*rows,usedW=cols*w+(cols-1)*gap,usedH=rows*h+(rows-1)*gap,waste=Math.max(0,pageW*pageH-usedW*usedH);return{pageWidthMm:pageW,pageHeightMm:pageH,orientation,cols,rows,perPage,usedW,usedH,waste,startX:(pageW-usedW)/2,startY:(pageH-usedH)/2};}
  function badgeLayout(opts,count){const w=Number(opts.widthMm)||74,h=Number(opts.heightMm)||105,margin=Math.max(0,Number(opts.marginMm)||0),gap=Math.max(0,Number(opts.gapMm)||0),requested=String(opts.pageOrientation||'auto');const portrait=pageCandidate(A4.widthMm,A4.heightMm,w,h,margin,gap,'portrait'),landscape=pageCandidate(A4.heightMm,A4.widthMm,w,h,margin,gap,'landscape');let best=requested==='portrait'?portrait:requested==='landscape'?landscape:(landscape.perPage>portrait.perPage?landscape:portrait.perPage>landscape.perPage?portrait:(landscape.waste<portrait.waste?landscape:portrait));return Object.assign({w,h,margin,gap,pages:Math.ceil(count/Math.max(1,best.perPage))},best);}

  function drawCutMarks(ctx,x,y,w,h,s){const X=mm(x,s),Y=mm(y,s),W=mm(w,s),H=mm(h,s),L=Math.max(1.8*s,6);ctx.save();ctx.strokeStyle='#8f969d';ctx.lineWidth=Math.max(.7,.06*s);ctx.setLineDash([]);[[X,Y,1,1],[X+W,Y,-1,1],[X,Y+H,1,-1],[X+W,Y+H,-1,-1]].forEach(([cx,cy,sx,sy])=>{ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(cx+sx*L,cy);ctx.moveTo(cx,cy);ctx.lineTo(cx,cy+sy*L);ctx.stroke();});ctx.restore();}
  function renderBadgePages(people,opts,visuals,onProgress){const scale=Number(opts.scale)||5,layout=badgeLayout(opts,people.length),pages=[];for(let p=0;p<layout.pages;p++){const c=canvasPage(layout.pageWidthMm,layout.pageHeightMm,scale),ctx=c.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,c.width,c.height);for(let i=0;i<layout.perPage;i++){const person=people[p*layout.perPage+i];if(!person)break;const col=i%layout.cols,row=Math.floor(i/layout.cols),x=layout.startX+col*(layout.w+layout.gap),y=layout.startY+row*(layout.h+layout.gap);drawBadge(ctx,person,x,y,layout.w,layout.h,scale,opts,visuals);if(opts.cutLines!==false)drawCutMarks(ctx,x,y,layout.w,layout.h,scale);}pages.push(c);emitProgress(onProgress,Math.round(((p+1)/Math.max(1,layout.pages))*100),'Rendering badge sheet '+(p+1)+' / '+layout.pages);}return {pages,layout};}
  function drawBadge(ctx,p,x,y,w,h,s,opts,v){
    const X=mm(x,s),Y=mm(y,s),W=mm(w,s),H=mm(h,s),m=Math.min(W,H);ctx.save();ctx.beginPath();ctx.rect(X,Y,W,H);ctx.clip();ctx.fillStyle='#fff';ctx.fillRect(X,Y,W,H);
    // Modernized version of the historical MIFP badge: institutional logo, conference artwork,
    // acronym, location/date, very clear participant identity, affiliation/country.
    const pad=Math.max(3*s,m*.046),topY=Y+pad,topH=H*.18;
    drawImageContain(ctx,v.organizerLogo,X+pad,topY,W*.30,topH);
    drawImageContain(ctx,v.conferenceLogo,X+W*.355,topY,W*.29,topH);
    const short=String(v.badgeName||v.shortName||'');
    const shortTop=fontFit(ctx,short,W*.28,Math.round(m*.064),Math.round(m*.038),'800',FONT_SANS);
    centerText(ctx,short,X+W*.68,Y+H*.092,W*.27,shortTop,'800','#111315',FONT_SANS);
    const confSize=fontFit(ctx,short,W-pad*2,Math.round(m*.070),Math.round(m*.046),'800',FONT_SANS);
    centerText(ctx,short,X+pad,Y+H*.285,W-pad*2,confSize,'800','#111315',FONT_SANS);
    const location=String(v.badgeLocation||'');
    if(location)centerText(ctx,location,X+pad,Y+H*.352,W-pad*2,fontFit(ctx,location,W-pad*2,Math.round(m*.037),Math.round(m*.025),'500',FONT_SANS),'500','#33383d',FONT_SANS);
    const date=String(v.date||'');
    if(date)centerText(ctx,date,X+pad,Y+H*.397,W-pad*2,fontFit(ctx,date,W-pad*2,Math.round(m*.034),Math.round(m*.023),'450',FONT_SANS),'450','#4d5359',FONT_SANS);
    const role=String(p.Category||p.Role||'').split(/[;,|]/).map(x=>x.trim()).filter(Boolean)[0]||'';
    if(role)centerText(ctx,role.toUpperCase(),X+pad,Y+H*.455,W-pad*2,fontFit(ctx,role.toUpperCase(),W-pad*2,Math.round(m*.026),Math.round(m*.019),'650',FONT_SANS),'650',v.accent||'#b5122b',FONT_SANS);
    const first=String(p['First Name']||p.Name||'').trim(),last=String(p['Last Name']||p.Surname||'').trim(),nameMax=W-pad*2;
    const firstSize=fontFit(ctx,first,nameMax,Math.round(m*.118),Math.round(m*.064),'800',FONT_SANS),lastSize=fontFit(ctx,last,nameMax,Math.round(m*.128),Math.round(m*.067),'800',FONT_SANS);
    centerText(ctx,first,X+pad,Y+H*.565,W-pad*2,firstSize,'800','#08090a',FONT_SANS);
    centerText(ctx,last,X+pad,Y+H*.660,W-pad*2,lastSize,'800','#08090a',FONT_SANS);
    centeredWrapped(ctx,String(p.Affiliation||''),X+pad,Y+H*.772,W-pad*2,Math.round(m*.039),Math.round(m*.035),'500','#30353a',2,FONT_SANS);
    centerText(ctx,String(p.Country||''),X+pad,Y+H*.822,W-pad*2,Math.round(m*.032),'450','#51575c',FONT_SANS);

    // Dedicated bottom strip: 1, 2 or 3 logos centered as a single group.
    const stripTop=Y+H*.852,stripH=H*.148;
    ctx.fillStyle='#fafbfc';ctx.fillRect(X,stripTop,W,stripH);
    ctx.strokeStyle='#d9dde1';ctx.lineWidth=Math.max(1,s*.16);ctx.beginPath();ctx.moveTo(X+pad,stripTop);ctx.lineTo(X+W-pad,stripTop);ctx.stroke();
    const logos=(Array.isArray(v.badgeBottomLogos)?v.badgeBottomLogos:[]).filter(Boolean).slice(0,3),n=logos.length;
    if(n){
      const groupRatio=n===1?.46:n===2?.72:.86,gapRatio=n===1?0:n===2?.045:.032,groupW=W*groupRatio,gap=W*gapRatio,cellW=(groupW-gap*(n-1))/n,startX=X+(W-groupW)/2,logoY=stripTop+stripH*.13,logoH=stripH*.74;
      logos.forEach((logo,index)=>drawImageContain(ctx,logo,startX+index*(cellW+gap),logoY,cellW,logoH));
    }
    ctx.restore();
  }

  function renderCertificatePages(people,opts,visuals,onProgress){
    const scale=Number(opts.scale)||5,pages=[];
    people.forEach((p,index)=>{
      const c=canvasA4(scale),ctx=c.getContext('2d'),S=scale,W=c.width,H=c.height;
      ctx.fillStyle='#fff';ctx.fillRect(0,0,W,H);

      // Keep a true 20 mm content margin, while the decorative frame sits near the page edge
      // like the historical MIFP certificates. This separates printable whitespace from ornament.
      const margin=Math.max(20,Number(visuals.certificateMarginMm)||26),left=margin,right=210-margin,contentW=(right-left)*S;
      const frameOuter=6.0,frameInner=7.15;
      ctx.save();
      ctx.strokeStyle='#111315';
      ctx.lineWidth=.28*S;
      ctx.strokeRect(frameOuter*S,frameOuter*S,W-2*frameOuter*S,H-2*frameOuter*S);
      ctx.lineWidth=.18*S;
      ctx.strokeRect(frameInner*S,frameInner*S,W-2*frameInner*S,H-2*frameInner*S);
      ctx.restore();

      // Institutional header: logo left, contacts right, green separator.
      drawImageContain(ctx,visuals.organizerLogo,left*S,10.2*S,34*S,18*S);
      const contact=[];
      if(visuals.organizerAddress)contact.push(String(visuals.organizerAddress));
      if(visuals.phone)contact.push('Phone: '+String(visuals.phone));
      if(visuals.email)contact.push('Email: '+String(visuals.email));
      rightLines(ctx,contact,right*S,12.5*S,2.9*S,4.15*S,'#202428',FONT_SANS);
      ctx.strokeStyle='#63aa3b';ctx.lineWidth=.40*S;ctx.beginPath();ctx.moveTo(left*S,33.4*S);ctx.lineTo(right*S,33.4*S);ctx.stroke();

      // Main hierarchy follows the supplied historical certificates.
      centerText(ctx,'CERTIFICATE OF ATTENDANCE',left*S,44.5*S,contentW,7.15*S,'800','#08090a',FONT_SANS);
      centerText(ctx,'TO WHOM IT MAY CONCERN',left*S,59.8*S,contentW,3.65*S,'450','#24282c',FONT_SANS);
      centerText(ctx,'THE UNDERSIGNED DOCUMENT CERTIFIES THAT',left*S,66.7*S,contentW,3.55*S,'450','#24282c',FONT_SANS);

      const full=[p['First Name'],p['Last Name']].filter(Boolean).join(' ').trim();
      centerText(ctx,full,left*S,81.8*S,contentW,fontFit(ctx,full,contentW-6*S,8.5*S,5.7*S,'800',FONT_SANS),'800','#08090a',FONT_SANS);
      centerText(ctx,'Participated at the',left*S,100.3*S,contentW,3.95*S,'450','#2e3337',FONT_SANS);

      const certificateFullName=String(visuals.certificateFullName||visuals.fullName||'');
      let conferenceFont=5.15*S,fullLines=[];
      while(conferenceFont>=4.15*S){ctx.font=fontDecl('800',conferenceFont,FONT_SANS);fullLines=wrapLines(ctx,certificateFullName,contentW-2*S,0);if(fullLines.length<=2)break;conferenceFont-=.20*S;}
      if(!fullLines.length)fullLines=[''];
      const fullY=113.2*S,conferenceLineGap=5.8*S;
      fullLines.slice(0,3).forEach((line,i)=>centerText(ctx,line,(left+1)*S,fullY+i*conferenceLineGap,(right-left-2)*S,conferenceFont,'800','#111315',FONT_SANS));
      const shortY=fullY+Math.max(1,Math.min(3,fullLines.length))*conferenceLineGap+.9*S;
      centerText(ctx,visuals.certificateShortName||visuals.shortName,left*S,shortY,contentW,4.65*S,'800','#171a1d',FONT_SANS);

      const pres=p.__presentation||{};let bodyEnd=shortY/S+5;
      if(opts.includePresentation!==false&&pres.title){
        const presentationLine=String(pres.label||pres.prefix||'Presentation').trim();
        if(presentationLine)centerText(ctx,presentationLine,left*S,143.5*S,contentW,3.45*S,'450','#30353a',FONT_SANS);
        let presentationFont=4.55*S,titleLines=[];
        while(presentationFont>=3.65*S){ctx.font=fontDecl('750',presentationFont,FONT_SANS);titleLines=wrapLines(ctx,pres.title,contentW-4*S,0);if(titleLines.length<=3)break;presentationFont-=.18*S;}
        titleLines.slice(0,4).forEach((line,i)=>centerText(ctx,line,(left+2)*S,(155.7+i*5.45)*S,(right-left-4)*S,presentationFont,'750','#111315',FONT_SANS));
        bodyEnd=155.7+Math.max(1,Math.min(4,titleLines.length))*5.45;
      }

      // The historical layout deliberately leaves a broad quiet area before venue/date.
      const locY=Math.max(190.5,bodyEnd+16.5);
      centerText(ctx,'Held at '+String(visuals.certificateLocation||visuals.location||''),left*S,locY*S,contentW,3.85*S,'450','#292e32',FONT_SANS);
      centerText(ctx,String(visuals.date||''),left*S,(locY+6.4)*S,contentW,3.85*S,'450','#292e32',FONT_SANS);
      centerText(ctx,'On behalf of the Organizing and Scientific Committee',left*S,(locY+18.0)*S,contentW,3.35*S,'450','#30353a',FONT_SANS);

      const signatures=Array.isArray(visuals.signatures)?visuals.signatures.filter(x=>x&&(x.title||x.name||x.affiliation)):[],cols=Math.max(1,Math.min(2,Number(visuals.signatureColumns)||2));
      const signatureTop=Math.max(locY+28.5,226.5);
      if(signatures.length){
        // Signature columns deliberately sit farther toward the page edges than the main text.
        const signatureOuterMm=12,signatureRightMm=198,signatureGapMm=22;
        const rows=Math.ceil(signatures.length/cols),gapX=signatureGapMm*S,outer=signatureOuterMm*S,totalW=(signatureRightMm-signatureOuterMm)*S,cellW=(totalW-gapX*(cols-1))/cols;
        const compact=rows>2,blockH=(compact?13.0:16.0),rowGap=(compact?2.4:4.0);
        signatures.forEach((sig,i)=>{
          const col=i%cols,row=Math.floor(i/cols),x=outer+col*(cellW+gapX),top=signatureTop+row*(blockH+rowGap),title=String(sig.title||'').trim(),name=String(sig.name||'').trim(),aff=String(sig.affiliation||'').trim();
          // Match the reference: role first and bold, then name, then affiliation. No signature rule.
          if(title)centerText(ctx,title,x,(top+1.5)*S,cellW,fontFit(ctx,title,cellW*.94,3.25*S,2.20*S,'800',FONT_SANS),'800','#111315',FONT_SANS);
          if(name)centerText(ctx,name,x,(top+6.0)*S,cellW,fontFit(ctx,name,cellW*.94,3.15*S,2.25*S,'450',FONT_SANS),'450','#202428',FONT_SANS);
          if(aff)centerText(ctx,aff,x,(top+10.1)*S,cellW,fontFit(ctx,aff,cellW*.94,2.75*S,2.0*S,'450',FONT_SANS),'450','#343a3f',FONT_SANS);
        });
      }

      // Event artwork is centered below the signatures, with the existing user controls preserved.
      const centerLogoW=Math.max(12,Math.min(100,Number(visuals.certificateCenterLogoWidthMm)||44.0));
      const centerLogoX=105+Math.max(-60,Math.min(60,Number(visuals.certificateCenterLogoOffsetXmm)||0));
      const centerLogoY=246.0+Math.max(-35,Math.min(20,Number(visuals.certificateCenterLogoOffsetYmm)||0));
      const centerLogoH=Math.min(44,Math.max(18,centerLogoW*.85));
      drawImageContain(ctx,visuals.certificateCenterLogo,(centerLogoX-centerLogoW/2)*S,centerLogoY*S,centerLogoW*S,centerLogoH*S);

      // Optional stamp remains centered below the right-hand signature block.
      const stampSize=28.0,signatureOuterMm=12.0,signatureRightMm=198.0,signatureGapMm=22.0,signatureCellMm=((signatureRightMm-signatureOuterMm)-signatureGapMm)/2,rightSignatureCenter=signatureOuterMm+signatureCellMm+signatureGapMm+signatureCellMm/2;
      drawImageContain(ctx,visuals.certificateStamp,(rightSignatureCenter-stampSize/2)*S,244.5*S,stampSize*S,stampSize*S);

      pages.push(c);emitProgress(onProgress,Math.round(((index+1)/Math.max(1,people.length))*100),'Rendering certificate '+(index+1)+' / '+people.length);
    });
    return pages;
  }


  global.DocumentTools=Object.freeze({A4,docxPlaceholders,fillDocxTemplate,buildPdfFromCanvases,buildDocxFromCanvases,badgeLayout,renderBadgePages,renderCertificatePages});
})(window);
