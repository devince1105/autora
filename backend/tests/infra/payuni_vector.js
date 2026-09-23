// An independent implementation of PAYUNi's envelope, in Node, to check the Python one against.
// Mirrors the official PHP SDK: aes-256-gcm, base64(ciphertext) ":::" base64(tag), then hex.
const crypto = require("node:crypto");
const key = "0123456789abcdef0123456789abcdef";   // 32 chars, as a HashKey is
const iv = "abcdef0123456789";                     // 16 chars, as a HashIV is
const fields = { MerID: "SHOP123", MerTradeNo: "T20260923001", TradeAmt: "360", Timestamp: "1790000000" };
const query = new URLSearchParams(fields).toString();
const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
const ciphertext = Buffer.concat([cipher.update(query, "utf8"), cipher.final()]);
const tag = cipher.getAuthTag();
const joined = Buffer.concat([Buffer.from(ciphertext.toString("base64")), Buffer.from(":::"), Buffer.from(tag.toString("base64"))]);
const encryptInfo = joined.toString("hex");
const hashInfo = crypto.createHash("sha256").update(key + encryptInfo + iv).digest("hex").toUpperCase();
console.log(JSON.stringify({ key, iv, fields, encryptInfo, hashInfo }));
