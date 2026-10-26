# Linux için hazır PipeWire bileşenleriyle deneme

İlk uygulama [stereo-virtual-speakers.conf](stereo-virtual-speakers.conf) dosyasıdır. PipeWire'ın kendi `filter-chain` modülünü, `libmysofa` spatializer'ını ve sistemdeki MIT KEMAR SOFA dosyasını kullanır. Sol ve sağ stereo kanalları ayrı ayrı öndeki ±30° sanal hoparlörlere yerleştirir; dört kulak sinyalini iki mixer'da birleştirir. Ek C++ konvolüsyon kodu yoktur. Bu, stereo müzik sanallaştırmasıdır; kayıtta bulunmayan bağımsız ses nesneleri üretilmez.

Bu makinede gerekli dosyalar mevcut:

```text
/usr/lib/pipewire-0.3/libpipewire-module-filter-chain.so
/usr/lib/spa-0.2/filter-graph/libspa-filter-graph-plugin-sofa.so
/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa
```

SOFA verisini canlı ses oturumu olmadan kontrol etmek için mevcut `libmysofa` API'sini kullanan [probe_sofa_hrtf.cpp](probe_sofa_hrtf.cpp) dosyası da var:

```sh
c++ -std=c++17 aerospatial/pipewire/probe_sofa_hrtf.cpp $(pkg-config --cflags --libs libmysofa) -o /tmp/probe_sofa_hrtf
/tmp/probe_sofa_hrtf
```

Bu makinede 48 kHz'de 558 örneklik filtre yüklendi. +30° için sol/sağ filtre enerjisi 2.386/0.341; 330° için 0.341/2.386 çıktı. Sağ/sol yön değişiminin veride karşılığı var. `delay_L/R = 0` bilgisi ITD yokluğu olarak yorumlanmamalı; gecikme filtre örneklerinin içinde olabilir. Bu kontrol kulaklıkta ses duyulduğunu göstermez.

## Canlı masaüstü oturumunda deneme

Steam Audio C API (`phonon.h`) kullanan C++ motorunun kısa canlı denemesi için, kulaklık gerçek varsayılan çıkışken repo kökünden şunu çalıştırın:

```sh
bash tools/run_linux_steam_audio_smoke.sh
```

Betik geçici sanal düğümü açar, çıkışlarını mevcut varsayılan fiziksel cihaza bağlar, sol–merkez–sağ ton sırasını çalar ve oynatma sırasındaki PipeWire bağlantılarını `aerospatial/build/live-smoke/` içine kaydeder. Merkez bölümü iki kanala eş fazlı verilir; iki ±30° sanal hoparlörün phantom center imgesini sınar. `sudo` kullanmayın; komutu kulaklığın bağlı olduğu masaüstü oturumundaki terminalde çalıştırın. Bu yatay stereo sanallaştırma denemesidir; yükseklik konumunu sınamaz.

## Tek uygulamayı AeroSpatial'e yönlendirme

Spotify PipeWire akışını geçici olarak yönlendirmek için:

```sh
bash tools/route_pipewire_app_through_aerospatial.sh
```

Betik Spotify'ın mevcut linklerini kaydeder, Spotify akışını sanal hoparlöre bağlar ve Ctrl+C ile önceki linkleri geri yükler. Farklı bir uygulamanın PipeWire node adı ilk argüman olarak verilebilir. Kullanıcı canlı Spotify denemesinde sesin boğuklaştığını, önceki EQ'nun uygulanmadığını ve görüntünün merkezde toplandığını bildirdi. Betik eski Spotify hedeflerinden EasyEffects'i ayırdığı için EQ'nun atlanması mevcut yönlendirmeyle uyumludur; bu zincir henüz korunmuyor. Merkez görüntüsü ve tını sorunu da çözülmüş değil. Bu aşama başarısız dinleme kabulü olarak kaydedildi; mevcut betiği günlük kullanım için önermiyoruz.

Aşağıdaki PipeWire SOFA denemesi, hazır sistem bileşenleriyle ayrı bir karşılaştırma yoludur.

1. `wpctl status` ile gerçek kulaklığın PipeWire'da göründüğünü doğrulayın. Gerçek kulaklığı varsayılan çıkış yapın; sanal çıkış henüz varsayılan olmasın.
2. Repo kökünde bir terminalde çalıştırın:

   ```sh
   pipewire -c "$PWD/aerospatial/pipewire/stereo-virtual-speakers.conf"
   ```

   Bu yalnızca süreç açıkken sanal çıkışı sunar; kullanıcı yapılandırma dosyası kurulmaz. `wpctl status` ile **AeroSpatial Stereo Virtual Speakers** çıkışını bulun. `pw-link -l` ile `effect_output.aerospatial_stereo_sofa` çıkışının gerçek kulaklığa gittiğini kontrol edin. Farklı hedefe gidiyorsa bağlantıyı düzeltmeden ses oynatmayın.
3. Kısa bir dosyayı sanal çıkışa yönlendirin:

   ```sh
   pw-play --target effect_input.aerospatial_stereo_sofa /path/to/stereo-test.wav
   ```

   Tek ses duyulmalı. `pw-link -l` içinde oynatıcı → sanal giriş → gerçek kulaklık yolu görülmeli; sanal çıkışın kendi girişine dönen bağlantı olmamalı. `pw-record` veya başka uygulamalarla daha sonra dosya bazlı kanal karşılaştırması yapılabilir.
4. Yol çalışırsa Spotify uygulama akışını bu sanal çıkışa taşıyıp aynı kontrolleri tekrarlayın. Sistem varsayılanını değiştirmek yerine önce yalnız Spotify'ı taşımak, diğer uygulamaların sesini etkilemeden denemeyi sağlar. `wpctl status` uygulama akışını ve seçili çıkışı gösterir.
5. Deneme sonunda ilk terminalde `Ctrl+C` ile sanal çıkışı kaldırın. Gerekirse varsayılan çıkışı gerçek kulaklığa geri seçin.

## Bu oturumda elde edilen kanıt

- Steam Audio tabanlı mevcut C++ hedefi yeniden derlendi; dört offline DSP testi geçti. Bu, PipeWire akışını doğrulamaz.
- Hazır PipeWire modülü, SOFA eklentisi ve KEMAR dosyası yerel diskte bulundu. Konfigürasyon `pw-config` ile okunabildi.
- `libmysofa` ile yukarıdaki iki yönlü filtre okuma denemesi geçti; yönler karşı kulakta aynı sonucu üretmiyor.
- Smoke testi için kaydedilen bağlantı grafiğinde `pw-play` AeroSpatial girişlerine ve AeroSpatial WH-1000XM4'e bağlıydı. Fakat o çalıştırmanın WAV hedefi eski dosya adıydı ve `wpctl` çıkış seviyesi `0.00` görünüyordu. Dosya adı ve Unicode satır öneki nedeniyle sink-ID eşleme sonradan düzeltildi; düzeltilmiş sol–merkez–sağ sinyal canlı olarak henüz doğrulanmadı.
- Spotify canlı denemesi kullanıcı dinleme kanıtıdır: ses boğuk, EasyEffects EQ kayıp ve merkez görüntüsü daralmış. Sistem sesi kaydı ve seviye eşlenmiş A/B ölçümü olmadığı için boğukluk ve merkez yığılmasının kök nedeni henüz kanıtlanmadı. Spotify'ın önceki linklerini kesip AeroSpatial'e doğrudan bağlamak EQ'nun bypass edilmesini açıklıyor. Linux Aşama 2 kabul edilmedi.

Sonraki denemede EQ-only, spatial-only, EQ→spatial ve doğrudan bypass zincirlerini aynı kaynak ve aynı çıkış seviyesinde karşılaştırın. PipeWire graph'ta her test kaynağı için tek fiziksel çıkış yolu bulunduğunu kaydedin. Gecikme ölçümü ertelenmiştir.

Kaynak: [PipeWire filter-chain ve SOFA spatializer](https://docs.pipewire.org/page_module_filter_chain.html).
