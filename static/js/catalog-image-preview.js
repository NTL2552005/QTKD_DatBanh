const imageInput = document.querySelector("#id_image");
const preview = document.querySelector("#image-preview");
const previewImage = document.querySelector("#image-preview-image");
let previewUrl;

if (imageInput && preview && previewImage) {
    imageInput.addEventListener("change", () => {
        if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
            previewUrl = undefined;
        }

        const [file] = imageInput.files;
        if (!file) {
            preview.hidden = true;
            previewImage.removeAttribute("src");
            return;
        }

        previewUrl = URL.createObjectURL(file);
        previewImage.src = previewUrl;
        preview.hidden = false;
    });
}
