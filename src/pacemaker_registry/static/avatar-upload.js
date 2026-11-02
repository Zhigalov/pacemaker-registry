/* Prepare an avatar before it reaches the cloud proxy's request-size limit. */
(function (root) {
  'use strict';
  const MAX_SOURCE = 5 * 1024 * 1024;
  const MAX_OUTPUT = 1024 * 1024;
  async function prepare(file) {
    if (!file.size || file.size > MAX_SOURCE) throw new Error('Выберите фотографию размером до 5 МБ.');
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('Выберите JPEG, PNG или WebP.');
    const url = URL.createObjectURL(file);
    try {
      const image = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error('Не удалось прочитать фотографию. Выберите другой файл.'));
        img.src = url;
      });
      if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth * image.naturalHeight > 16000000) {
        throw new Error('Фотография слишком большая: максимум 16 мегапикселей.');
      }
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 512;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Не удалось подготовить фото. Попробуйте другой браузер.');
      context.fillStyle = '#fff';
      context.fillRect(0, 0, 512, 512);
      const side = Math.min(image.naturalWidth, image.naturalHeight);
      context.drawImage(image, (image.naturalWidth - side) / 2, (image.naturalHeight - side) / 2, side, side, 0, 0, 512, 512);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .88));
      if (!blob || !blob.size || blob.size > MAX_OUTPUT) throw new Error('Не удалось уменьшить фото. Выберите другой файл.');
      return blob;
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  root.PacersAvatar = { prepare };
})(globalThis);
