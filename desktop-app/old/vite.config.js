import { defineConfig } from 'vite'

// https://vitejs.dev/config/
export default defineConfig({
  // Явно указываем, что корень нашего фронтенда находится в папке 'frontend'
  root: 'frontend',
  build: {
    // Указываем, куда Vite должен складывать собранные файлы
    outDir: '../dist'
  }
})