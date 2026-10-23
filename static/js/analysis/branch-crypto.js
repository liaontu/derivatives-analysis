// Shared by the browser and the dependency-free Node collector. Private keys never upload.
const encoder = new TextEncoder();
const format = 'RSA-OAEP-256/A256GCM';
const b64 = bytes => btoa(Array.from(new Uint8Array(bytes), x => String.fromCharCode(x)).join(''));
const bytes = value => Uint8Array.from(atob(value), c => c.charCodeAt(0));

export async function keyId(jwk) {
  const digest = await crypto.subtle.digest('SHA-256', encoder.encode(JSON.stringify({n:jwk.n,e:jwk.e})));
  return b64(digest).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
}

export async function seal(payload, publicJwk) {
  if (publicJwk.d || publicJwk.kty !== 'RSA') throw new Error('Public RSA key required');
  const id = await keyId(publicJwk);
  const rsa = await crypto.subtle.importKey('jwk',publicJwk,{name:'RSA-OAEP',hash:'SHA-256'},false,['encrypt']);
  const aes = await crypto.subtle.generateKey({name:'AES-GCM',length:256},true,['encrypt']);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode(format+'|'+id)},aes,encoder.encode(JSON.stringify(payload)));
  const wrappedKey = await crypto.subtle.encrypt('RSA-OAEP',rsa,await crypto.subtle.exportKey('raw',aes));
  return {version:1,algorithm:format,keyId:id,iv:b64(iv),wrappedKey:b64(wrappedKey),ciphertext:b64(ciphertext)};
}

export async function unlockKey(file) {
  if (file.version !== 1 || file.kind !== 'derivatives-branch-unlock' || !file.privateKey?.d) throw new Error('解鎖檔格式不正確');
  const id = await keyId(file.privateKey);
  const key = await crypto.subtle.importKey('jwk',file.privateKey,{name:'RSA-OAEP',hash:'SHA-256'},false,['decrypt']);
  return {id,key};
}

export async function unseal(envelope, unlock) {
  if (envelope.version !== 1 || envelope.algorithm !== format || envelope.keyId !== unlock.id) throw new Error('這份解鎖檔不適用目前的資料');
  const raw = await crypto.subtle.decrypt('RSA-OAEP',unlock.key,bytes(envelope.wrappedKey));
  const aes = await crypto.subtle.importKey('raw',raw,'AES-GCM',false,['decrypt']);
  const clear = await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes(envelope.iv),additionalData:encoder.encode(format+'|'+unlock.id)},aes,bytes(envelope.ciphertext));
  return JSON.parse(new TextDecoder().decode(clear));
}
