const fileInput = document.querySelector("#file");
const selectedFileName = document.querySelector("#selected-file-name");

if (fileInput && selectedFileName) {
  fileInput.addEventListener("change", () => {
    selectedFileName.textContent = fileInput.files.length
      ? fileInput.files[0].name
      : "No file selected";
    selectedFileName.classList.toggle("has-file", fileInput.files.length > 0);
  });
}
