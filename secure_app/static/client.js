(() => {
"use strict";
const $ = id => document.getElementById(id);
let roomId = "", secret = "", aesKey = null, verifier = "", socket = null;
const enc = new TextEncoder(), dec = new TextDecoder();
const b64u = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");
const unb64u = s => Uint8Array.from(atob(s.replace(/-/g,"+").replace(/_/g,"/")+"=".repeat((4-s.length%4)%4)),c=>c.charCodeAt(0));
function randomToken(n=24){return b64u(crypto.getRandomValues(new Uint8Array(n)));}
async function derive(){
  const material = await crypto.subtle.importKey("raw",enc.encode(secret),"PBKDF2",false,["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({name:"PBKDF2",salt:enc.encode("private-chat-v1:"+roomId),iterations:600000,hash:"SHA-256"},material,512);
  const bytes = new Uint8Array(bits);
  aesKey = await crypto.subtle.importKey("raw",bytes.slice(0,32),{name:"AES-GCM"},false,["encrypt","decrypt"]);
  const hmacKey = await crypto.subtle.importKey("raw",bytes.slice(32),{name:"HMAC",hash:"SHA-256"},false,["sign"]);
  verifier = b64u(await crypto.subtle.sign("HMAC",hmacKey,enc.encode("room-auth:"+roomId)));
}
function parseCode(value){
  const parts=value.trim().split(".");
  if(parts.length!==2 || !/^[A-Za-z0-9_-]{20,32}$/.test(parts[0]) || !/^[A-Za-z0-9_-]{30,64}$/.test(parts[1])) throw new Error("Mã phòng không đúng định dạng.");
  roomId=parts[0];secret=parts[1];
}
async function createRoom(){
  roomId=randomToken(16);secret=randomToken(32);await derive();
  const res=await fetch("/api/rooms",{method:"POST",headers:{"Content-Type":"application/json"},cache:"no-store",body:JSON.stringify({room_id:roomId,verifier})});
  if(!res.ok) throw new Error("Không tạo được phòng. Vui lòng thử lại.");
  $("share").textContent="Gửi mã này cho đúng một người qua kênh riêng: "+roomId+"."+secret;
  await connect();
}
async function connect(){
  await derive();
  socket=new WebSocket((location.protocol==="https:"?"wss://":"ws://")+location.host+"/ws/"+roomId);
  socket.onopen=()=>socket.send(JSON.stringify({type:"auth",verifier}));
  socket.onmessage=async event=>{
    const p=JSON.parse(event.data);
    if(p.type==="ready"){ $("setup").classList.add("hidden");$("chat").classList.remove("hidden");$("status").textContent="Đã kết nối. Không lưu lịch sử.";return; }
    if(p.type==="ciphertext"){
      try{
        const plain=await crypto.subtle.decrypt({name:"AES-GCM",iv:unb64u(p.iv)},aesKey,unb64u(p.ciphertext));
        addMessage(dec.decode(plain));
      }catch(_){$("status").textContent="Không giải mã được tin nhắn (sai bí mật hoặc dữ liệu bị thay đổi).";}
    }
  };
  socket.onclose=()=>{$("status").textContent="Kết nối đã đóng. Phòng có thể đã bị hủy.";};
  socket.onerror=()=>{$("status").textContent="Lỗi kết nối. Kiểm tra HTTPS và thử lại.";};
}
function addMessage(text){const div=document.createElement("div");div.className="msg";div.textContent=text;$("messages").appendChild(div);$("messages").scrollTop=$("messages").scrollHeight;}
async function send(){
  const value=$("message").value.trim();if(!value||!socket||socket.readyState!==WebSocket.OPEN)return;
  if(value.length>3000)return;
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const ciphertext=await crypto.subtle.encrypt({name:"AES-GCM",iv},aesKey,enc.encode(value));
  socket.send(JSON.stringify({type:"ciphertext",iv:b64u(iv),ciphertext:b64u(ciphertext)}));
  addMessage("Bạn: "+value);$("message").value="";
}
$("create").onclick=async()=>{try{$("create").disabled=true;await createRoom();}catch(e){alert(e.message);$("create").disabled=false;}};
$("code").addEventListener("input",()=>{$("join").classList.toggle("hidden",!$("code").value.trim());});
$("join").onclick=async()=>{try{parseCode($("code").value);await connect();}catch(e){alert(e.message);}};
$("send").onclick=send;$("message").addEventListener("keydown",e=>{if(e.key==="Enter")send();});
$("leave").onclick=()=>{if(socket)socket.close();aesKey=null;secret="";verifier="";$("messages").replaceChildren();$("chat").classList.add("hidden");$("setup").classList.remove("hidden");$("share").textContent="";$("code").value="";};
window.addEventListener("beforeunload",()=>{if(socket)socket.close();aesKey=null;});
})();