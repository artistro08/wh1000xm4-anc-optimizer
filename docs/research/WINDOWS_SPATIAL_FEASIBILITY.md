# Windows sistem sesi için binaural işleme fizibilitesi

Tarih: 2026-10-25. Kapsam: Spotify ve seçilen çıkışa ses veren uygulamalar; masaüstü Windows. Dört birincil belge incelendi. TinyFish önceki ortam denemesinde kullanılamadığından web okuma aracı kullanıldı. Bu rapor mimari değerlendirmedir; Windows üzerinde doğrulama veya gecikme ölçümü yapılmadı.

**Karar:** İlk Windows doğrulamasında mevcut Equalizer APO üzerinden sabit binaural filtre/profil uygulama yolu araştırılsın. Ortak C++ çekirdeğin doğrudan entegrasyonu için eklenti arayüzü ve lisans doğrulanmadan uygulama taahhüdü verilmesin. Bağımsız ürün için sanal ses aygıtı + WASAPI köprüsü ikinci seçenek olsun; yeni sürücü geliştirme ilk aşamaya alınmasın.

| Yol | Akış ve yararı | Maliyet / sınır |
|---|---|---|
| Sanal render endpoint + WASAPI | Uygulamalar → sanal çıkış → yakalama → ortak C++ binaural DSP → fiziksel kulaklık. DSP kullanıcı sürecinde tutulur; Linux/Android ile paylaşılması kolaydır. | Sanal aygıtın sürücüsü, kurulumu ve dağıtımı ayrıca gerekir. Kaynak ve fiziksel çıkış saatleri farklıysa tampon yönetimi/yeniden örnekleme gerekir. Fiziksel çıkışa doğrudan yönelen uygulama işlemi atlar. |
| Özel APO | C++ çekirdeği bir Windows COM/APO adaptörü sarar; efekt ses işleme grafiğine yerleşir. Ayrı yakalama/yeniden çalma köprüsü gerekmez. | Gerçek zamanlı çalışma kuralları, INF/aygıt ilişkilendirmesi ve kurulum gerekir. Her endpoint ve işleme modu ayrı doğrulanmalıdır. |
| Equalizer APO entegrasyonu | Mevcut APO kurulumu üzerine profil/filtre dağıtımı, ilk denemede yeni sürücü geliştirme yükünü azaltabilir. | Configurator ile doğru aygıt seçilmeli; kurulum sonrası yeniden başlatma gerekir. OEM APO çakışmaları ve kapalı ses geliştirmeleri işlemi engelleyebilir. Mevcut belge doğrudan C++ eklenti sözleşmesini doğrulamıyor. |

APO'lar kullanıcı modunda çalışan gerçek zamanlı COM nesneleridir. SFX akış başına, MFX mod başına, EFX endpoint düzeyinde konumlanır. RAW akışlar bazı SFX/MFX yollarını atlayabilir; Microsoft'un EFX için RAW kapsamı açıklaması her alternatif ses API'sinin garantiyle desteklendiği anlamına gelmez. Özel APO, INF üzerinden ses grafiğine eklenebilir. [Microsoft APO mimarisi](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/audio-processing-object-architecture)

**Loopback tek başına sesin yerine geçmez.** WASAPI fiziksel çıkışa giden sesi yakalama tamponuna da kopyalar; özgün sesi kesmez. Yakalananı işleyip aynı çıkışa göndermek çift ses ve yeniden yakalama döngüsü doğurabilir. Önerilen sanal aygıt tasarımında özgün ses yalnızca sanal çıkışa yönlenir, işlenen ses farklı fiziksel çıkışta çalınır. Loopback yalnızca shared mode destekler; exclusive akışlar ve korumalı içerik için evrensel yakalama sözü verilemez. Güvenilir sürücüler korumalı dijital içeriğin yakalanmasını engelleyebilir. Spotify adı tek başına belirli bir koruma yolunun kullanıldığını kanıtlamaz. [Microsoft loopback belgesi](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)

“Sistem geneli” ilk sürümde **seçilen endpoint'e yönlenen, doğrulanmış shared-mode uygulamalar** olarak tanımlanmalı. Exclusive/ASIO ve korumalı yollar kabul kapsamı dışında bırakılmalı; bunların tamamında aynı davranış varsayılmamalı. Equalizer APO'nun OEM efektleriyle birlikte çalışması ve geliştirmelerin açık olması gerekir; OEM efektlerini devre dışı bırakmak mevcut işlevleri kaybettirebilir. [Equalizer APO kurulum ve sorun giderme](https://sourceforge.net/p/equalizerapo/wiki/Documentation/)

**Dağıtım ve lisans kapısı:** Özel sanal sürücünün üretim imzalama süreci ayrı iş paketidir; yerel geliştirme kurulumu ürün dağıtımı sayılmaz. Microsoft, attestation veya WHCP gönderimi için dashboard hesabıyla ilişkili en az bir geçerli EV sertifikası ister; gönderi imzalama sertifikası ve SHA-2 şartları ayrıca uygulanır. Bu belge belirli bir paket için hangi sertifikasyon yolunun yeterli olduğunu tek başına belirlemez. [Microsoft sürücü imzalama şartları](https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/code-signing-reqs)

İncelenen Equalizer APO kullanım belgesi kesin lisans sürümünü ve yeniden dağıtım koşullarını doğrulamıyor. Seçilecek sürümün kaynak lisansı, varsa eklenti SDK lisansı, sanal sürücünün yeniden dağıtım hakkı ve HRTF/BRIR verisinin lisansı sürüm kilidiyle kaydedilmeden paketleme yapılmamalı. GPL uyumluluğu veya ticari yeniden dağıtım hakkı varsayılmamalı. Kullanıcının bağımsız kurduğu yazılımla yapılandırma paylaşımı ve kod/binary birleştirme ayrı incelenmeli.

**Uygulama öncesi geçiş koşulları:**

1. Windows/XM4 üzerinde Spotify ve tarayıcı shared-mode sesinde efektin gerçekten etkin olduğunu, bypass dönüşünü ve çift ses olmadığını doğrula.
2. 44,1/48 kHz, mono/stereo, aygıt çıkarma, Bluetooth yeniden bağlanma ve varsayılan aygıt değişiminde güvenli toparlanmayı doğrula.
3. Exclusive, RAW, geliştirmeler kapalı ve korumalı akış durumlarında destek sınırını görünür kıl; sessizce “işleniyor” gösterme.
4. Ortak C++ DSP'de platform API'lerini dışarıda tut; blok işleme, format dönüşümü ve parametre güncelleme adaptörlerde olsun. Ses callback'inde ayırma, dosya erişimi ve bloklayan kilit kullanma.
5. Equalizer APO için sabit filtreyle yeterlilik ve dinamik çekirdek entegrasyonu ayrı kararlar olsun. Doğrudan entegrasyon sözleşmesi doğrulanamazsa sanal endpoint köprüsüne geç.
6. Temiz kurulum/kaldırma, yeniden başlatma sonrası endpoint ilişkilendirmesi, imza ve bileşen/veri lisanslarını doğrula. Gecikme ölçümü bu araştırmanın kapsamında değildir.
