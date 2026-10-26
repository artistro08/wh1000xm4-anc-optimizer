# Android genelinde kulaklık uzamsallaştırma: uygulanabilirlik

25 Ekim 2026 — Yalnızca planlama; cihaz, Spotify ve gecikme testi yapılmadı. TinyFish bulunamadığından resmî kaynaklar web aracıyla okundu.

**Karar:** Standart, rootsuz bir uygulamanın Spotify dahil bütün uygulamaların sesini zorunlu olarak kendi DSP hattından geçireceği vaat edilemez. Önce telefonun/OEM'in mevcut uzamsallaştırması değerlendirilmeli; özel DSP için kaynak erişimi ve tek çıkış doğrulanmalıdır.

## API sınırları

- **AudioPlaybackCapture (Android 10+):** Sesi kopyalar. `RECORD_AUDIO`, kullanıcı onaylı `MediaProjection`, aynı kullanıcı profili, uygun kullanım türü ve kaynakta `ALLOW_CAPTURE_BY_ALL` gerekir; en kısıtlayıcı politika geçerlidir. Yakalama izni kaynak uygulamaya zorla verilemez. API açıklaması kaynak çıkışını ele geçirme/susturma sunmaz. Dolayısıyla yakalanan sesi yeniden oynatmak, kaynak ayrıca duyuluyorsa çift ses üretir; medya sesini kapatmak işlenen çıkışı da etkileyebilir ve çözüm sayılmaz. Spotify'ın güncel yakalama politikası ve korumalı içerik davranışı burada doğrulanmadı. DRM varlığından tek başına kesin sonuç çıkarılmaz; bu API DRM çözme/atlatma yolu değildir. [Android playback capture](https://developer.android.com/media/platform/av-capture)
- **AudioEffect:** Belirli `AudioTrack`/`MediaPlayer` oturum kimliğine bağlanır. Global miks için session `0` üzerinden insert efekt kullanımı deprecated durumundadır. Bu, her cihazda kesinlikle çalışmaz demek değildir; taşınabilir sistem geneli mimari garantisi değildir. Mevcut efekt denetimi, keyfî PCM yakalama veya özel HRTF motoru yerleştirme yetkisi anlamına gelmez. [AudioEffect](https://developer.android.com/reference/android/media/audiofx/AudioEffect)
- **Spatializer:** Cihazın isteğe bağlı uzamsallaştırma desteği, durumu ve biçim uygunluğunu sorgular; destek kulaklık bağlantısıyla değişebilir. Genel uygulama API'si başka uygulamaların PCM verisini özel DSP'ye teslim etmez. NDK/C++ kullanmak da capture politikasını kaldıran bir yetkilendirme yöntemi değildir; native DSP ancak erişilebilen ses üzerinde çalışacak şekilde planlanmalıdır. [Spatializer](https://developer.android.com/reference/android/media/Spatializer), [native capture policy dahil Android kılavuzu](https://developer.android.com/media/platform/av-capture)

## Uygulanabilir yollar

| Yol | Kapsam ve koşul |
| --- | --- |
| Telefonun mevcut OEM uzamsallaştırması | En düşük geliştirme yükü; hedef telefon, XM4 çıkışı ve stereo içerik desteğine bağlı. Özel HRTF kontrolü varsayılmaz. |
| Kendi oynatıcımız + native DSP | Kontrolümüzdeki, çözümlenebilir PCM için uygulanabilir. Spotify'ın kendi uygulamasına sistem geneli etki sağlamaz. |
| Rootsuz yakala–işle–oynat | Yalnızca kaynak yakalamaya izin verirse ve kaynak sesini yakalamayı bozmadan tek çıkışa indirme yolu kanıtlanırsa aday. Aksi halde ürün yolu elenir. |
| OEM/sistem efekt entegrasyonu | AOSP cihaz efektleri vendor kütüphanesi, `audio_effects.xml` ve sistem/HAL entegrasyonu ister. Root/özel ROM, bu katmanda cihaz özelinde araştırılabilecek ayrı bir yoldur; root tek başına başarı garantisi değildir. |

Son satırın OEM gereksinimleri resmî belgede açıklanır; root/özel ROM seçeneği buradan yapılan mühendislik çıkarımıdır. [AOSP audio effects](https://source.android.com/docs/core/audio/audio-effects)

## Uygulama öncesi üç doğrulama kapısı

1. **Cihaz ve mevcut yol:** Telefon modeli, Android/OEM sürümü, root durumu ve XM4 bağlantı türü belirlenir; mevcut uzamsallaştırmanın hedef stereo içerikte kullanılabilirliği kaydedilir.
2. **Kaynağa erişim:** Spotify'ın hedef sürümünde ve istenen içerikte izinli PCM yakalama veya kullanılabilir efekt oturumu gerçekten var mı doğrulanır; yoksa rootsuz özel DSP yolu durdurulur.
3. **Tek işlenmiş çıkış:** Orijinal sesin ayrıca duyulmadığı, işlenen çıkışın kendini tekrar yakalamadığı ve kulaklık yeniden bağlanınca yolun korunabildiği doğrulanır. Çözülmezse OEM/kendi oynatıcı/ayrı root araştırması seçilir.

Bu kapılar gelecekteki uygulanabilirlik doğrulamasıdır; bu çalışma kapsamında test veya gecikme ölçümü yapılmaz.
