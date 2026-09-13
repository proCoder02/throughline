import { apiJson, post } from '../api.js';

/// Presigned-URL direct upload to Cloudflare R2 -- the Flask backend never
/// receives the file bytes, it only ever hands out a short-lived PUT URL
/// (see /uploads/presign in app.py). `purpose` is one of
/// 'profile_picture'|'chat_image'|'chat_video'|'chat_file' (storage.py's
/// _PURPOSES). Returns the object_key; call confirmUpload() after to get
/// back the real public URL once the bytes have actually landed.
export async function uploadFile(file, purpose, onProgress) {
  const { upload_url, object_key } = await post('/uploads/presign', {
    purpose, content_type: file.type || 'application/octet-stream',
  });
  await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', upload_url);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error('Upload failed')));
    xhr.onerror = () => reject(new Error('Upload failed'));
    xhr.send(file);
    // XHR (not fetch) specifically for the upload.onprogress event -- fetch
    // has no upload-progress API as of this writing.
  });
  return object_key;
}

export async function confirmUpload(objectKey, purpose) {
  const { public_url } = await post('/uploads/confirm', { object_key: objectKey, purpose });
  return public_url;
}

export function getUploadsStatus() {
  return apiJson('/uploads/status').catch(() => ({ enabled: false }));
}

/// Downscaled, low-quality JPEG as a base64 data: URL -- sent inline with
/// the message itself (see the thumbnail_data_url schema column), never
/// uploaded to R2. `source` can be an image File/Blob, or an
/// already-captured video frame (see captureVideoFrame below).
export async function makeThumbnailDataUrl(source, maxWidth = 160) {
  const bitmap = await createImageBitmap(source);
  const scale = Math.min(1, maxWidth / bitmap.width);
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close?.();
  return canvas.toDataURL('image/jpeg', 0.5); // quality 0.5 -- a preview, not the delivered image
}

/// Grabs the first frame of a video File as a Blob, for makeThumbnailDataUrl
/// to downscale the same way it does an image. Uses an off-DOM <video> +
/// <canvas> pair; cleans both up once done.
export function captureVideoFrame(videoFile) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(videoFile);
    const video = document.createElement('video');
    video.src = url;
    video.muted = true;
    video.playsInline = true;
    const cleanup = () => URL.revokeObjectURL(url);
    video.onloadeddata = () => {
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext('2d').drawImage(video, 0, 0);
      canvas.toBlob((blob) => { cleanup(); (blob ? resolve(blob) : reject(new Error('No frame captured'))); }, 'image/jpeg', 0.7);
    };
    video.onerror = () => { cleanup(); reject(new Error('Could not read video')); };
  });
}
