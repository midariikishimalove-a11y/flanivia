# Baixador de vídeos TikTok e Instagram

Site simples, feito para computador e celular: você cola o link do vídeo, toca em **Baixar** e recebe o arquivo em HD, sem marca d'água.

## Arquivos

- `app.py`: servidor (Flask + yt-dlp) que baixa o vídeo
- `index.html`: a página do site
- `requirements.txt`: dependências
- `render.yaml` e `Procfile`: configuração para colocar o site online

## Colocar online para usar no celular (Render, plano grátis)

O GitHub só guarda o código, ele não roda o site. Para abrir no celular, o site precisa estar em um servidor. O jeito mais fácil:

1. Suba estes arquivos para um repositório no GitHub.
2. Crie uma conta grátis em render.com (pode entrar com o GitHub).
3. Clique em **New +** e depois em **Blueprint** (ou **Web Service**) e escolha o seu repositório.
   - Com Blueprint, o `render.yaml` já configura tudo sozinho.
   - Com Web Service, use:
     - Build Command: `pip install -r requirements.txt`
     - Start Command: `gunicorn app:app --timeout 120 --workers 2`
4. Espere terminar. O Render vai te dar um endereço como `https://baixador-de-video.onrender.com`.
5. Abra esse endereço no celular. No Chrome (Android), use o menu ⋮ e **Adicionar à tela inicial**. No iPhone, no Safari, use o botão de compartilhar e **Adicionar à Tela de Início**. Fica como um aplicativo.

Observações do plano grátis do Render: o site "dorme" depois de uns minutos sem uso, então o primeiro acesso pode demorar cerca de 1 minuto. Os próximos são rápidos.

Dica: TikTok e Instagram às vezes bloqueiam endereços de servidores na nuvem. Se o site online falhar e no seu computador funcionar, esse costuma ser o motivo.

## Rodar no computador (opcional)

1. Instale o Python 3.9 ou mais novo.
2. No terminal, dentro desta pasta:

   ```
   pip install -r requirements.txt
   python app.py
   ```

3. Abra http://localhost:5000 no navegador.

(Opcional) Instale o **ffmpeg**: ajuda o yt-dlp com alguns vídeos do Instagram.

## Como o download funciona no celular

- **Android**: o vídeo vai para a pasta Downloads. Também aparece o botão "Salvar no celular", que abre o menu de compartilhar.
- **iPhone**: toque em "Salvar no celular" e escolha **Salvar vídeo** para mandar para o app Fotos. Sem isso, o vídeo vai para o app Arquivos.

## Observações

- Só funciona com posts **públicos**. Vídeos privados ou que exigem login falham.
- TikTok e Instagram mudam as coisas com frequência. Se parar de funcionar, atualize o yt-dlp: `pip install -U yt-dlp` (no Render, é só fazer um novo deploy, que ele pega a versão mais nova).
- Sobre a marca d'água do TikTok: o programa pede a versão sem marca. Se o TikTok só entregar a versão com marca para algum vídeo específico, ele baixa o que estiver disponível.
- O Instagram não coloca marca d'água no vídeo original, então o download já vem limpo.
- Use só para conteúdo seu ou com permissão do autor. Redistribuir vídeo de outras pessoas pode violar direitos autorais e os termos das plataformas.
- Se for deixar o site aberto para outras pessoas, considere limitar o número de downloads por IP para o servidor não ser abusado.
