# Baixador (visual Windows 7)

Site com 3 abas, que funciona no computador e no celular:

- **Redes sociais:** TikTok e Instagram, em HD e sem marca d'água
- **YouTube:** vídeo em MP4 (você escolhe a resolução) ou áudio em MP3 (você escolhe a qualidade). Playlists inteiras viram um ZIP.
- **Spotify:** música, álbum ou playlist em MP3. Álbum e playlist viram ZIP.

## Arquivos

| Arquivo | Para que serve |
|---|---|
| `index.html` | A página (visual, abas, barra de progresso) |
| `app.py` | O servidor que baixa tudo |
| `requirements.txt` | Lista dos programas que o servidor usa |
| `render.yaml`, `Procfile` | Configuração para colocar online no Render |
| `iniciar.bat` | Atalho para rodar no Windows |
| `.gitignore` | Impede que arquivos secretos (cookies) subam para o GitHub |

## Importante: onde rodar

TikTok, Instagram e principalmente o **YouTube** costumam **bloquear servidores na nuvem** (como o Render). No Render grátis, é provável que o YouTube peça login e que o TikTok recuse. Já no **seu computador, com a internet de casa**, quase sempre funciona.

O jeito mais confiável de usar também no celular:

1. Rode no seu computador (veja abaixo).
2. No celular, ligado no **mesmo Wi-Fi**, abra o endereço que aparece no terminal, algo como `http://192.168.0.15:5000`.

## Rodar no computador (Windows)

1. Instale o Python em python.org (marque **Add Python to PATH**).
2. Dê dois cliques em `iniciar.bat`.
3. Abra o endereço que aparecer na janela preta. Se o Windows perguntar sobre o firewall, permita (é isso que deixa o celular acessar).

Em outros sistemas: `pip install -r requirements.txt` e depois `python app.py`.

## Colocar online no Render (opcional)

1. Suba todos os arquivos para o GitHub (o `index.html` precisa se chamar exatamente `index.html`).
2. No Render, o serviço atual atualiza sozinho quando você troca os arquivos. Se for novo: **New + > Blueprint** e escolha o repositório.
3. Espere o deploy. A primeira instalação demora mais, porque agora instala conversor de áudio (ffmpeg) e um motor de JavaScript (deno) que o YouTube exige.
4. Abra `/saude` no seu endereço (ex.: `https://seu-site.onrender.com/saude?rede=1`). Ele mostra se tudo foi instalado e se TikTok, YouTube e Spotify respondem a esse servidor. Se aparecer `403` ou erro, é bloqueio.

O plano grátis tem 512 MB de memória e "dorme" após 15 minutos parado, por isso os limites padrão são pequenos (veja abaixo).

## Se o YouTube pedir login (cookies)

Mensagem: *"O YouTube bloqueou este servidor e pediu login"*. A saída é entregar ao servidor os cookies de uma conta Google:

1. Exporte os cookies do YouTube para um arquivo `cookies.txt` (formato Netscape). Cuidado: algumas extensões de "exportar cookies" já foram usadas para roubar dados. Use uma de código aberto e confiável.
2. **Use uma conta secundária, não a sua principal.** Baixar muito com uma conta pode fazer o Google limitar ou bloquear essa conta.
3. No Render: **Environment > Secret Files**, crie o arquivo `cookies.txt` com o conteúdo. O site o encontra sozinho em `/etc/secrets/cookies.txt`. No seu computador, defina a variável `COOKIES_FILE` com o caminho.
4. **Nunca coloque o `cookies.txt` no GitHub.** O repositório é público e qualquer um poderia usar sua conta.

Os cookies duram cerca de 2 semanas.

## Spotify: como funciona (e limites)

O Spotify protege o áudio, então **não dá para baixar direto dele**. O site lê a lista de músicas (nome, artista, duração), procura cada uma no YouTube, escolhe o resultado mais parecido e baixa o áudio de lá. Por isso:

- a versão pode ser diferente da do Spotify (ao vivo, outra gravação);
- depende do YouTube funcionar no servidor (veja a seção acima);
- ler a lista usa a página pública do Spotify, que ele muda de vez em quando. Se parar de funcionar, crie uma chave grátis em developer.spotify.com e defina `SPOTIFY_CLIENT_ID` e `SPOTIFY_CLIENT_SECRET`.

## Qualidade

- **Vídeo:** a lista de resoluções vem do próprio vídeo. Até 1080p o arquivo sai em H.264, que toca em qualquer celular. 1440p e 4K saem em outro formato (VP9/AV1) dentro do MP4, e alguns iPhones não tocam.
- **MP3:** você escolhe 128, 192, 256 ou 320 kbps, mas o YouTube guarda o áudio em ~128–160 kbps. 320 não melhora a qualidade, só aumenta o arquivo.

## Limites (mude com variáveis de ambiente)

| Variável | Padrão | O que faz |
|---|---|---|
| `MAX_ITENS_AUDIO` | 40 | Músicas por ZIP |
| `MAX_ITENS_VIDEO` | 10 | Vídeos por ZIP |
| `MAX_MINUTOS` | 60 | Duração máxima de cada item |
| `MAX_JOBS` | 2 (1 no Render) | Downloads ao mesmo tempo |
| `MAX_JOBS_POR_IP` | 2 | Downloads ao mesmo tempo por pessoa |
| `VALIDADE_MIN` | 20 | Minutos que o arquivo fica disponível |

No seu computador você pode aumentar à vontade (por exemplo `MAX_ITENS_AUDIO=200`).

## Avisos

- Funciona só com conteúdo **público**.
- Baixar músicas e vídeos de outras pessoas pode violar direitos autorais e os termos de TikTok, Instagram, YouTube e Spotify. Use só com conteúdo seu, livre ou com permissão de quem criou.
- Se parar de funcionar, atualize o yt-dlp: `pip install -U yt-dlp` (no Render, é só fazer um novo deploy).
